import json
from types import SimpleNamespace

from mistralai.client import Mistral
from django.conf import settings

from ai_providers.agent_loop import run_agent_loop
from ai_providers.base import AIProviderBase, ProviderResponse, ToolCall, UsageAccumulator


class MistralProvider(AIProviderBase):
    label = "Mistral"
    AVAILABLE_MODELS = [
        {"id": "mistral-large-latest", "label": "Mistral Large"},
        {"id": "mistral-medium-latest", "label": "Mistral Medium"},
        {"id": "mistral-small-latest", "label": "Mistral Small"},
        {"id": "ministral-8b-latest", "label": "Ministral 8B"},
    ]
    # USD per 1M tokens.
    PRICING = {
        "mistral-large-latest": {"input": 0.50, "output": 1.50},
        "mistral-medium-latest": {"input": 1.50, "output": 7.50},
        "mistral-small-latest": {"input": 0.15, "output": 0.60},
        "ministral-8b-latest": {"input": 0.15, "output": 0.15},
    }

    def __init__(self, api_key: str | None = None):
        self.client = Mistral(api_key=api_key or settings.MISTRAL_API_KEY)

    @staticmethod
    def _normalize_arguments(arguments) -> dict:
        # Mistral's SDK types `arguments` as `str | dict` depending on the call path.
        return json.loads(arguments) if isinstance(arguments, str) else arguments

    def _build_kwargs(self, assistant, messages, system, tools):
        kwargs = {
            "model": assistant.model,
            "max_tokens": 8192,
            "messages": [{"role": "system", "content": system or assistant.instructions}, *messages],
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in tools
            ]
        return kwargs

    @staticmethod
    def _to_provider_response(raw) -> ProviderResponse:
        message = raw.choices[0].message
        tool_calls = [
            ToolCall(id=call.id, name=call.function.name, arguments=MistralProvider._normalize_arguments(call.function.arguments))
            for call in (message.tool_calls or [])
        ]
        usage = {"input_tokens": raw.usage.prompt_tokens, "output_tokens": raw.usage.completion_tokens}
        return ProviderResponse(text=message.content or "", tool_calls=tool_calls, raw=raw, usage=usage)

    async def complete(self, assistant, messages, system, tools) -> ProviderResponse:
        kwargs = self._build_kwargs(assistant, messages, system, tools)
        raw = await self.client.chat.complete_async(**kwargs)
        return self._to_provider_response(raw)

    def append_turn(self, messages, response: ProviderResponse, tool_results=None) -> list[dict]:
        message = response.raw.choices[0].message
        assistant_message = {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in (message.tool_calls or [])
            ],
        }
        tool_messages = [
            {"role": "tool", "tool_call_id": call_id, "content": str(result)}
            for call_id, result in (tool_results or [])
        ]
        return [*messages, assistant_message, *tool_messages]

    async def stream(
        self, assistant, messages, system, tools, tool_executor,
        usage: UsageAccumulator | None = None, on_tool_call=None,
    ):
        kwargs = self._build_kwargs(assistant, messages, system, tools)

        text_parts = []
        tool_call_chunks: dict[int, dict] = {}
        raw_usage = None

        event_stream = await self.client.chat.stream_async(**kwargs)
        async for event in event_stream:
            chunk = event.data
            if chunk.usage:
                raw_usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                text_parts.append(delta.content)
                yield delta.content
            for tc_delta in delta.tool_calls or []:
                index = tc_delta.index or 0
                acc = tool_call_chunks.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if tc_delta.id:
                    acc["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        acc["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        acc["arguments"] += tc_delta.function.arguments

        tool_calls = [
            ToolCall(id=acc["id"], name=acc["name"], arguments=json.loads(acc["arguments"]) if acc["arguments"] else {})
            for acc in tool_call_chunks.values()
        ]
        usage_dict = (
            {"input_tokens": raw_usage.prompt_tokens, "output_tokens": raw_usage.completion_tokens}
            if raw_usage else {"input_tokens": 0, "output_tokens": 0}
        )
        if usage is not None:
            usage.add(**usage_dict)

        raw_message = SimpleNamespace(
            content="".join(text_parts) or None,
            tool_calls=[
                SimpleNamespace(id=call.id, function=SimpleNamespace(name=call.name, arguments=call.arguments))
                for call in tool_calls
            ] or None,
        )
        response = ProviderResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            raw=SimpleNamespace(choices=[SimpleNamespace(message=raw_message)]),
            usage=usage_dict,
        )
        if response.requires_tool_execution and tool_executor:
            text = await run_agent_loop(
                self, assistant, messages, system, tools, tool_executor,
                initial_response=response, usage=usage, on_tool_call=on_tool_call,
            )
            yield text
