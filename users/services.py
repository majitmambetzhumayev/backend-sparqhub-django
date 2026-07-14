# users/services.py
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from users.tasks import send_email_task

User = get_user_model()

_RESET_EMAIL_SUBJECT = "Reset your SparqHub password"
_CONFIRMATION_EMAIL_SUBJECT = "Confirm your SparqHub email"


class EmailConfirmationTokenGenerator(PasswordResetTokenGenerator):
    # Distinct key_salt so a confirmation token can never be replayed as a
    # password-reset token (or vice versa) even though both share the same
    # underlying HMAC secret.
    key_salt = "users.services.EmailConfirmationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        # Folding email_verified into the hash (unlike the base class, which
        # only tracks password/last_login) makes the token single-use: once
        # confirm_email() flips it to True, the same token no longer
        # validates.
        return f"{user.pk}{timestamp}{user.email_verified}"


email_confirmation_token_generator = EmailConfirmationTokenGenerator()


def request_password_reset(email: str) -> None:
    """Silently no-ops if the email isn't registered — never reveal whether
    an email exists in the system via this endpoint's behavior."""
    user = User.objects.filter(email=email).first()
    if user is None:
        return
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = f"{settings.FRONTEND_URL.rstrip('/')}/auth/reset-password?uid={uid}&token={token}"
    html = (
        f"<p>Click the link below to reset your SparqHub password. "
        f"This link expires soon and can only be used once.</p>"
        f'<p><a href="{link}">{link}</a></p>'
    )
    send_email_task.delay(user.email, _RESET_EMAIL_SUBJECT, html)


def confirm_password_reset(uid: str, token: str, new_password: str) -> bool:
    """Returns False for any invalid uid/token combination — deliberately
    doesn't distinguish "bad uid" from "bad/expired token" in its return
    value, so callers can't be used to enumerate valid uids either."""
    try:
        user_pk = urlsafe_base64_decode(uid).decode()
        user = User.objects.get(pk=user_pk)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        return False
    if not default_token_generator.check_token(user, token):
        return False
    user.set_password(new_password)
    user.save(update_fields=["password"])
    return True


def send_confirmation_email(user) -> None:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_confirmation_token_generator.make_token(user)
    link = f"{settings.FRONTEND_URL.rstrip('/')}/auth/confirm-email?uid={uid}&token={token}"
    html = (
        f"<p>Click the link below to confirm your SparqHub email address.</p>"
        f'<p><a href="{link}">{link}</a></p>'
    )
    send_email_task.delay(user.email, _CONFIRMATION_EMAIL_SUBJECT, html)


def confirm_email(uid: str, token: str) -> bool:
    """Same non-distinguishing-failure shape as confirm_password_reset — see
    that function's docstring."""
    try:
        user_pk = urlsafe_base64_decode(uid).decode()
        user = User.objects.get(pk=user_pk)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        return False
    if not email_confirmation_token_generator.check_token(user, token):
        return False
    user.email_verified = True
    user.save(update_fields=["email_verified"])
    return True


def _generate_unique_username(email: str) -> str:
    base = email.split('@')[0] or 'user'
    username = base
    suffix = 1
    while User.objects.filter(username=username).exists():
        suffix += 1
        username = f"{base}{suffix}"
    return username


def get_or_create_oauth_user(provider: str, provider_user_id: str, email: str):
    """provider is 'google' or 'github' — matches the CustomUser.<provider>_id
    field name. Both providers verify email ownership themselves, so a match
    (by provider id, then by email) or a fresh account is always marked
    email_verified — this is the one path that bypasses the confirmation
    email entirely.

    An existing account found by email that was NOT already verified is
    reclaimed, not just trusted as-is: anyone can register with any email
    address before ever proving they own it (registration sends a
    confirmation link, but nothing stops someone from registering with
    someone else's email and leaving it unconfirmed — a pre-account-
    hijacking setup). OAuth login is itself proof of ownership, stronger
    than clicking a confirmation link since the provider authenticated the
    person directly — so on first OAuth login for a matching-but-unverified
    account, take it over and invalidate whatever password may already be
    set on it, rather than trusting a password nobody vouched for. (A fresh
    separate account isn't an option either way — email has a DB-level
    uniqueness constraint.) Once verified, later logins here (a second OAuth
    provider linking to the same, already-legitimate account) don't touch
    the password again."""
    id_field = f"{provider}_id"

    user = User.objects.filter(**{id_field: provider_user_id}).first()
    if user is not None:
        return user

    user = User.objects.filter(email=email).first()
    if user is not None:
        setattr(user, id_field, provider_user_id)
        update_fields = [id_field]
        if not user.email_verified:
            user.email_verified = True
            user.set_unusable_password()
            update_fields += ["email_verified", "password"]
        user.save(update_fields=update_fields)
        return user

    username = _generate_unique_username(email)
    return User.objects.create_user(
        username=username,
        email=email,
        email_verified=True,
        **{id_field: provider_user_id},
    )
