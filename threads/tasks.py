import logging

from asgiref.sync import async_to_sync
from celery import shared_task

from threads.models import Thread
from threads.services import generate_and_store_title

logger = logging.getLogger(__name__)


@shared_task
def generate_thread_title_task(thread_id: int, user_text: str, assistant_text: str) -> None:
    try:
        thread = Thread.objects.select_related('user').get(pk=thread_id)
        async_to_sync(generate_and_store_title)(thread, user_text, assistant_text)
    except Exception:
        logger.exception("Title generation failed for thread %s", thread_id)
