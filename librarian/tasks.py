import logging

from asgiref.sync import async_to_sync
from celery import shared_task
from django.contrib.auth import get_user_model

from assistants.models import Assistant
from librarian.services import extract_and_store_memories

logger = logging.getLogger(__name__)


@shared_task
def extract_memories_task(user_id: int, assistant_id: int, user_text: str, assistant_text: str) -> None:
    try:
        user = get_user_model().objects.get(pk=user_id)
        assistant = Assistant.objects.get(pk=assistant_id)
        async_to_sync(extract_and_store_memories)(user, assistant, user_text, assistant_text)
    except Exception:
        logger.exception("Memory extraction failed for user %s", user_id)
