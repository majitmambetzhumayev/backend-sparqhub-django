# ai_providers/openai/interface.py
import logging
from ai_providers.base import AIProviderBase
from .assistant_api import create_agent_and_record, soft_delete_local
from .chat_api import chat_with_agent
from .sdk_utils import apply_openai_key

logger = logging.getLogger(__name__)

class OpenAIAssistantInterface(AIProviderBase):
    # Set supports_crud to True so our view uses our create/update methods.
    supports_crud = True

    def __init__(self, user=None):
        apply_openai_key(user)

    def create_assistant(self, user, name, instructions, model="gpt-4o"):
        return create_agent_and_record(user, name, instructions, model)

    def update_assistant(self, assistant_id, user, name=None, instructions=None, model=None):
        from assistants.models import Assistant
        try:
            assistant = Assistant.objects.get(provider_assistant_id=assistant_id, user=user)
        except Assistant.DoesNotExist:
            logger.error("Assistant with ID %s not found for user %s", assistant_id, user)
            raise Exception("Assistant not found")
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
        return chat_with_agent(assistant, message_text, stream)
