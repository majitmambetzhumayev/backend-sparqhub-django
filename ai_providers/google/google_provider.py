from google import genai
from google.genai import types
from django.conf import settings

from ai_providers.agent_loop import run_agent_loop
from ai_providers.base import AIProviderBase, ProviderResponse, ToolCall, UsageAccumulator


class GeminiProvider(AIProviderBase):
    label = "Gemini"
    AVAILABLE_MODELS = [
        {"id": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro (Preview)"},
        {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
        {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
        {"id": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite"},
    ]
    # USD per 1M tokens.
    PRICING = {
        "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00},
        "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
        "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
        "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    }

    def __init__(self, api_key: str | None = None):
        self.client = genai.Client(api_key=api_key or settings.GEMINI_API_KEY)
        # Gemini's "thinking" models attach an opaque thought_signature to each
        # function-call Part; it must be echoed back verbatim when the tool
        # result is sent in the next turn, or the API rejects the request.
        # Scoped per-instance since a fresh provider is created per turn.
        self._thought_signatures: dict[str, bytes] = {}
        # This SDK's FunctionCall.id is consistently None, so synthetic ids are
        # needed. They must be unique across the whole turn, not just within a
        # single response's parts — otherwise two separate rounds of the same
        # tool call collide on the same id and silently corrupt each other's
        # thought_signature (the exact failure: "missing thought_signature...
        # position 2", from a second round-trip overwriting the first's entry).
        self._call_counter = 0

    @staticmethod
    def _to_part(part: dict) -> "types.Part":
        if "text" in part:
            return types.Part(text=part["text"])
        if "function_call" in part:
            call = part["function_call"]
            return types.Part(
                function_call=types.FunctionCall(name=call["name"], args=call["args"], id=call.get("id")),
                thought_signature=call.get("thought_signature"),
            )
        if "function_response" in part:
            resp = part["function_response"]
            return types.Part(function_response=types.FunctionResponse(name=resp["name"], response=resp["response"], id=resp.get("id")))
        raise ValueError(f"Unrecognized message part: {part}")

    def _to_content(self, message: dict) -> "types.Content":
        if "parts" in message:
            return types.Content(role=message["role"], parts=[self._to_part(p) for p in message["parts"]])
        role = "model" if message["role"] == "assistant" else message["role"]
        return types.Content(role=role, parts=[types.Part(text=message["content"])])

    def _build_kwargs(self, assistant, messages, system, tools):
        config_kwargs = {"system_instruction": system or assistant.instructions, "max_output_tokens": 8192}
        if tools:
            config_kwargs["tools"] = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=tool["name"],
                            description=tool.get("description", ""),
                            parameters_json_schema=tool["input_schema"],
                        )
                        for tool in tools
                    ]
                )
            ]
        return {
            "model": assistant.model,
            "contents": [self._to_content(m) for m in messages],
            "config": types.GenerateContentConfig(**config_kwargs),
        }

    def _extract_tool_calls_from_parts(self, parts) -> list[ToolCall]:
        tool_calls = []
        for part in parts or []:
            call = part.function_call
            if call is None:
                continue
            if call.id:
                call_id = call.id
            else:
                call_id = f"{call.name}_{self._call_counter}"
                self._call_counter += 1
            if part.thought_signature:
                self._thought_signatures[call_id] = part.thought_signature
            tool_calls.append(ToolCall(id=call_id, name=call.name, arguments=call.args or {}))
        return tool_calls

    def _to_provider_response(self, raw) -> ProviderResponse:
        parts = raw.candidates[0].content.parts if raw.candidates and raw.candidates[0].content else []
        tool_calls = self._extract_tool_calls_from_parts(parts)
        usage = {
            "input_tokens": raw.usage_metadata.prompt_token_count or 0,
            "output_tokens": raw.usage_metadata.candidates_token_count or 0,
        }
        return ProviderResponse(text=raw.text or "", tool_calls=tool_calls, raw=raw, usage=usage)

    async def complete(self, assistant, messages, system, tools) -> ProviderResponse:
        kwargs = self._build_kwargs(assistant, messages, system, tools)
        raw = await self.client.aio.models.generate_content(**kwargs)
        return self._to_provider_response(raw)

    def append_turn(self, messages, response: ProviderResponse, tool_results=None) -> list[dict]:
        call_names = {call.id: call.name for call in response.tool_calls}
        model_parts = []
        if response.text:
            model_parts.append({"text": response.text})
        for call in response.tool_calls:
            model_parts.append({
                "function_call": {
                    "name": call.name,
                    "args": call.arguments,
                    "id": call.id,
                    "thought_signature": self._thought_signatures.get(call.id),
                },
            })
        model_message = {"role": "model", "parts": model_parts}

        result_parts = [
            {"function_response": {"name": call_names[call_id], "response": {"result": result}, "id": call_id}}
            for call_id, result in (tool_results or [])
        ]
        tool_message = {"role": "user", "parts": result_parts}
        return [*messages, model_message, tool_message]

    async def stream(
        self, assistant, messages, system, tools, tool_executor,
        usage: UsageAccumulator | None = None, on_tool_call=None,
    ):
        kwargs = self._build_kwargs(assistant, messages, system, tools)

        text_parts = []
        tool_calls: list[ToolCall] = []
        raw_usage = None

        stream = await self.client.aio.models.generate_content_stream(**kwargs)
        async for chunk in stream:
            if chunk.text:
                text_parts.append(chunk.text)
                yield chunk.text
            if chunk.candidates and chunk.candidates[0].content:
                tool_calls.extend(self._extract_tool_calls_from_parts(chunk.candidates[0].content.parts))
            if chunk.usage_metadata:
                raw_usage = chunk.usage_metadata

        usage_dict = (
            {
                "input_tokens": raw_usage.prompt_token_count or 0,
                "output_tokens": raw_usage.candidates_token_count or 0,
            }
            if raw_usage else {"input_tokens": 0, "output_tokens": 0}
        )
        if usage is not None:
            usage.add(**usage_dict)

        response = ProviderResponse(text="".join(text_parts), tool_calls=tool_calls, raw=None, usage=usage_dict)
        if response.requires_tool_execution and tool_executor:
            text = await run_agent_loop(
                self, assistant, messages, system, tools, tool_executor,
                initial_response=response, usage=usage, on_tool_call=on_tool_call,
            )
            yield text
