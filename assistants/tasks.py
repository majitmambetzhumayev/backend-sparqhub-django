# assistants/tasks.py
from celery import shared_task
import asyncio

from assistants.models import Assistant
from ai_providers.openai.async_chat import chat_async

@shared_task
def summarize_thread(assistant_id: int, message_text: str) -> str:
    """
    Celery task that runs your agent in “batch”/async mode
    and returns the final output string.
    """
    # 1) Load the Assistant from the database
    assistant = Assistant.objects.get(id=assistant_id)

    # 2) Run the async chat helper and block until it’s done
    output: str = asyncio.run(chat_async(assistant, message_text))

    # 3) Return or store the output as needed
    return output
