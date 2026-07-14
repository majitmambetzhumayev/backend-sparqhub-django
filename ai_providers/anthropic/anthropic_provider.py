import anthropic
from django.conf import settings

from ai_providers.agent_loop import run_agent_loop
from ai_providers.base import (
    AIProviderBase, ProviderResponse, ToolCall, UsageAccumulator, warn_if_finish_reason_suspicious,
)


class AnthropicProvider(AIProviderBase):
    label = "Anthropic"
    AVAILABLE_MODELS = [
        {"id": "claude-opus-4-8", "label": "Claude Opus 4.8"},
        {"id": "claude-sonnet-5", "label": "Claude Sonnet 5"},
        {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5"},
        {"id": "claude-fable-5", "label": "Claude Fable 5"},
    ]
    # USD per 1M tokens, standard rate (Sonnet 5's temporary intro pricing is
    # intentionally ignored so this table doesn't need a revisit in 2 months).
    PRICING = {
        "claude-opus-4-8": {"input": 5.00, "output": 25.00},
        "claude-sonnet-5": {"input": 3.00, "output": 15.00},
        "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
        "claude-fable-5": {"input": 10.00, "output": 50.00},
    }

    def __init__(self, api_key: str | None = None):
        self.client = anthropic.AsyncAnthropic(api_key=api_key or settings.ANTHROPIC_API_KEY)

    def _build_kwargs(self, assistant, messages, system, tools):
        kwargs = {
            "model": assistant.model,
            "max_tokens": 8192,
            "system": system or assistant.instructions,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        return kwargs

    @staticmethod
    def _to_provider_response(raw) -> ProviderResponse:
        text = ""
        tool_calls = []
        for block in raw.content:
            if block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
            elif hasattr(block, "text"):
                text = block.text
        usage = {"input_tokens": raw.usage.input_tokens, "output_tokens": raw.usage.output_tokens}
        return ProviderResponse(text=text, tool_calls=tool_calls, raw=raw, usage=usage, finish_reason=raw.stop_reason)

    async def complete(self, assistant, messages, system, tools) -> ProviderResponse:
        kwargs = self._build_kwargs(assistant, messages, system, tools)
        raw = await self.client.messages.create(**kwargs)
        return self._to_provider_response(raw)

    def append_turn(self, messages, response: ProviderResponse, tool_results=None) -> list[dict]:
        tool_result_blocks = [
            {"type": "tool_result", "tool_use_id": call_id, "content": str(result)}
            for call_id, result in (tool_results or [])
        ]
        return [
            *messages,
            {"role": "assistant", "content": response.raw.content},
            {"role": "user", "content": tool_result_blocks},
        ]

    async def stream(
        self, assistant, messages, system, tools, tool_executor,
        usage: UsageAccumulator | None = None, on_tool_call=None,
    ):
        kwargs = self._build_kwargs(assistant, messages, system, tools)
        async with self.client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
            raw = await stream.get_final_message()

        response = self._to_provider_response(raw)
        if usage is not None:
            usage.add(**response.usage)
        if response.requires_tool_execution and tool_executor:
            text = await run_agent_loop(
                self, assistant, messages, system, tools, tool_executor,
                initial_response=response, usage=usage, on_tool_call=on_tool_call,
            )
            yield text
        else:
            # No tool call this turn, so run_agent_loop (which does its own
            # check on the final response) never runs — this is the only
            # place a plain streamed reply's finish_reason is ever seen.
            warn_if_finish_reason_suspicious(response)
