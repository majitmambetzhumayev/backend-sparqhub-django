#ai_providers/chat_router.py
from ai_providers.factory import get_provider
import logging

logger = logging.getLogger(__name__)

def send_chat_message(assistant, message_text, stream=False):
    """
    Dispatches a chat request to the correct provider based on the assistant's configuration.
    """
    try:
        provider = get_provider(assistant.ai_provider)
        response = provider.chat(assistant, message_text, stream=stream)
        return response
    except Exception as e:
        logger.exception("Error during chat dispatch: %s", e)
        raise e
