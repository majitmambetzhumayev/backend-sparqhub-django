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
