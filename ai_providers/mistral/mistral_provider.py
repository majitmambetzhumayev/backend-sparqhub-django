import logging
from ..base import AIProviderBase
from assistants.models import Assistant

logger = logging.getLogger(__name__)

class MistralProvider(AIProviderBase):
    supports_crud = True  # Mistral allows assistant management

    def create_assistant(self, user, name, instructions, model="mistral-large"):
        response = self._call_mistral_api_create(name, instructions, model)
        assistant = Assistant.objects.create(
            user=user,
            name=name,
            provider_assistant_id=response["id"],  # Assuming API returns an ID
            instructions=instructions,
            model=model,
            ai_provider="mistral",
            is_persistent=True
        )
        return assistant

    def update_assistant(self, assistant_id, user, name=None, instructions=None, model=None):
        response = self._call_mistral_api_update(assistant_id, name, instructions, model)
        assistant = Assistant.objects.get(provider_assistant_id=assistant_id, user=user)
        assistant.name = response["name"]
        assistant.instructions = response["instructions"]
        assistant.model = response["model"]
        assistant.save()
        return assistant

    def delete_assistant(self, assistant_id, user):
        self._call_mistral_api_delete(assistant_id)
        assistant = Assistant.objects.get(provider_assistant_id=assistant_id, user=user)
        assistant.deleted = True
        assistant.save()
