import anthropic
from django.conf import settings
from ai_providers.base import AIProviderBase


class AnthropicProvider(AIProviderBase):
    supports_crud = False

    def __init__(self):
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def chat(self, assistant, messages, stream=False):
        response = await self.client.messages.create(
            model=assistant.model,
            max_tokens=1024,
            system=assistant.instructions,
            messages=messages,
        )
        return response.content[0].text
