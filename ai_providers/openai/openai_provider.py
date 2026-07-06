import json
from types import SimpleNamespace

from openai import AsyncOpenAI
from django.conf import settings

from ai_providers.agent_loop import run_agent_loop
from ai_providers.base import AIProviderBase, ProviderResponse, ToolCall, UsageAccumulator


class OpenAIProvider(AIProviderBase):
    label = "OpenAI"
    AVAILABLE_MODELS = [
        {"id": "gpt-5.5", "label": "GPT-5.5"},
        {"id": "gpt-5.4", "label": "GPT-5.4"},
        {"id": "gpt-5.4-mini", "label": "GPT-5.4 Mini"},
        {"id": "gpt-5.4-nano", "label": "GPT-5.4 Nano"},
    ]
    # USD per 1M tokens.
    PRICING = {
        "gpt-5.5": {"input": 5.00, "output": 30.00},
        "gpt-5.4": {"input": 2.50, "output": 15.00},
        "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
        "gpt-5.4-nano": {"input": 0.20, "output": 1.25},
    }

    def __init__(self, api_key: str | None = None):
        self.client = AsyncOpenAI(api_key=api_key or settings.OPENAI_API_KEY)

    def _build_kwargs(self, assistant, messages, system, tools):
        kwargs = {
            "model": assistant.model,
            "max_completion_tokens": 8192,
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
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=json.loads(call.function.arguments) if call.function.arguments else {},
            )
            for call in (message.tool_calls or [])
        ]
        usage = {"input_tokens": raw.usage.prompt_tokens, "output_tokens": raw.usage.completion_tokens}
        return ProviderResponse(text=message.content or "", tool_calls=tool_calls, raw=raw, usage=usage)

    async def complete(self, assistant, messages, system, tools) -> ProviderResponse:
        kwargs = self._build_kwargs(assistant, messages, system, tools)
        raw = await self.client.chat.completions.create(**kwargs)
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
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        text_parts = []
        tool_call_chunks: dict[int, dict] = {}
        raw_usage = None

        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if chunk.usage:
                raw_usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                text_parts.append(delta.content)
                yield delta.content
            for tc_delta in delta.tool_calls or []:
                acc = tool_call_chunks.setdefault(tc_delta.index, {"id": "", "name": "", "arguments": ""})
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
                SimpleNamespace(id=call.id, function=SimpleNamespace(name=call.name, arguments=json.dumps(call.arguments)))
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
