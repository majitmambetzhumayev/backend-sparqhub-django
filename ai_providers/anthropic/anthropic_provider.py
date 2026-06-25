import anthropic
from django.conf import settings

from ai_providers.base import AIProviderBase


class AnthropicProvider(AIProviderBase):
    supports_crud = False

    def __init__(self):
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def chat(self, assistant, messages, system=None, stream=False, tools=None, tool_executor=None):
        kwargs = {
            "model": assistant.model,
            "max_tokens": 1024,
            "system": system or assistant.instructions,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self.client.messages.create(**kwargs)

        while response.stop_reason == "tool_use" and tool_executor:
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await tool_executor(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })

            messages = [
                *messages,
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": tool_results},
            ]
            response = await self.client.messages.create(**{**kwargs, "messages": messages})

        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""
