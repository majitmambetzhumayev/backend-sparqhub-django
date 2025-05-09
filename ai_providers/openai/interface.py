import logging
from ai_providers.base import AIProviderBase
from .assistant_api import create_agent_and_record, soft_delete_local
from .async_chat import chat_async
from .streaming_chat import stream_chat
from .sdk_utils import apply_openai_key

logger = logging.getLogger(__name__)

class OpenAIAssistantInterface(AIProviderBase):
    """
    Adapter for the OpenAI Agents SDK.
    """

    supports_crud = True

    def __init__(self, user=None):
        # Ensure environment is set before any SDK usage
        apply_openai_key(user)

    def create_assistant(self, user, name, instructions, model="gpt-4o"):
        return create_agent_and_record(user, name, instructions, model)

    def update_assistant(self, assistant_id, user, name=None, instructions=None, model=None):
        from assistants.models import Assistant
        try:
            assistant = Assistant.objects.get(provider_assistant_id=assistant_id, user=user)
        except Assistant.DoesNotExist:
            logger.error("Assistant not found: %s", assistant_id)
            raise

        if name:
            assistant.name = name
        if instructions:
            assistant.instructions = instructions
        if model:
            assistant.model = model
        assistant.save()
        return assistant

    def delete_assistant(self, assistant_id, user):
        return soft_delete_local(assistant_id, user)

    def chat(self, assistant, message_text, stream=False):
        if stream:
            # return async generator directly
            return stream_chat(assistant, message_text)
        else:
            # return an awaitable that your Celery task can call
            return chat_async(assistant, message_text)
