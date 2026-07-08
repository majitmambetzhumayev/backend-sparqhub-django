# users/services.py
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from users.tasks import send_email_task

User = get_user_model()

_RESET_EMAIL_SUBJECT = "Reset your SparqHub password"


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
