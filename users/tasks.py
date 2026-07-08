import logging

from celery import shared_task

from core.services import send_email

logger = logging.getLogger(__name__)


@shared_task
def send_email_task(to: str, subject: str, html: str) -> None:
    try:
        send_email(to, subject, html)
    except Exception:
        logger.exception("Failed to send email to %s", to)
