import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from ai_providers.google.google_provider import GeminiProvider
from ai_providers.base import ProviderResponse, ToolCall, UsageAccumulator


def run(coro):
    return asyncio.run(coro)


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for chunk in self._chunks:
            yield chunk


class _FunctionCall:
    def __init__(self, call_id, name, args):
        self.id = call_id
        self.name = name
        self.args = args


def _make_part(function_call=None, thought_signature=None):
    return MagicMock(function_call=function_call, thought_signature=thought_signature)


def _make_response(text=None, function_calls=None, thought_signatures=None, prompt_tokens=10, candidate_tokens=5):
    usage_metadata = MagicMock(prompt_token_count=prompt_tokens, candidates_token_count=candidate_tokens)
    function_calls = function_calls or []
    signatures = thought_signatures or [None] * len(function_calls)
    parts = [_make_part(function_call=fc, thought_signature=sig) for fc, sig in zip(function_calls, signatures)]
    content = MagicMock(parts=parts)
    candidate = MagicMock(content=content)
    return MagicMock(text=text, function_calls=function_calls, usage_metadata=usage_metadata, candidates=[candidate])


class GeminiProviderCompleteTest(SimpleTestCase):
    def setUp(self):
        with patch('ai_providers.google.google_provider.genai.Client'):
            self.provider = GeminiProvider()
        self.assistant = MagicMock()
        self.assistant.model = 'gemini-2.5-flash'
        self.assistant.instructions = 'Be helpful.'

    def test_complete_returns_text_response(self):
        self.provider.client.aio.models.generate_content = AsyncMock(return_value=_make_response(text='Hello!'))
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'Be helpful.', None))
        self.assertEqual(result.text, 'Hello!')
        self.assertFalse(result.requires_tool_execution)

    def test_aclose_closes_the_underlying_async_client(self):
        # Regression test: the genai SDK's async httpx client outlives a
        # single call if never closed explicitly, and garbage-collecting it
        # later — after a short-lived event loop (e.g. a Celery task run via
        # async_to_sync) has already closed — raises "Event loop is closed".
        self.provider.client.aio.aclose = AsyncMock()
        run(self.provider.aclose())
        self.provider.client.aio.aclose.assert_awaited_once()

    def test_complete_renames_assistant_role_to_model(self):
        self.provider.client.aio.models.generate_content = AsyncMock(return_value=_make_response(text='OK'))
        run(self.provider.complete(
            self.assistant,
            [{'role': 'user', 'content': 'Hi'}, {'role': 'assistant', 'content': 'Hey'}],
            'sys', None,
        ))
        call_kwargs = self.provider.client.aio.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs['contents'][1].role, 'model')

    def test_complete_translates_tools_param(self):
        self.provider.client.aio.models.generate_content = AsyncMock(return_value=_make_response(text='OK'))
        tools = [{'name': 'my_tool', 'description': 'does a thing', 'input_schema': {'type': 'object'}}]
        run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', tools))
        call_kwargs = self.provider.client.aio.models.generate_content.call_args.kwargs
        declarations = call_kwargs['config'].tools[0].function_declarations
        self.assertEqual(declarations[0].name, 'my_tool')
        self.assertEqual(declarations[0].parameters_json_schema, {'type': 'object'})

    def test_complete_extracts_tool_calls(self):
        self.provider.client.aio.models.generate_content = AsyncMock(
            return_value=_make_response(function_calls=[_FunctionCall('call_1', 'get_data', {'param': 'value'})])
        )
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Do it'}], 'sys', None))
        self.assertTrue(result.requires_tool_execution)
        self.assertEqual(result.tool_calls, [ToolCall(id='call_1', name='get_data', arguments={'param': 'value'})])

    def test_complete_falls_back_to_synthetic_id_when_missing(self):
        self.provider.client.aio.models.generate_content = AsyncMock(
            return_value=_make_response(function_calls=[_FunctionCall(None, 'get_data', {})])
        )
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Do it'}], 'sys', None))
        self.assertEqual(result.tool_calls[0].id, 'get_data_0')

    def test_complete_captures_usage(self):
        self.provider.client.aio.models.generate_content = AsyncMock(
            return_value=_make_response(text='Hi', prompt_tokens=42, candidate_tokens=17)
        )
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None))
        self.assertEqual(result.usage, {'input_tokens': 42, 'output_tokens': 17})

    def test_complete_captures_thought_signature_for_later_append_turn(self):
        # Gemini's "thinking" models require echoing back each function call's
        # thought_signature on the next turn, or the API rejects the request.
        self.provider.client.aio.models.generate_content = AsyncMock(return_value=_make_response(
            function_calls=[_FunctionCall('call_1', 'generate_image', {'prompt': 'a cat'})],
            thought_signatures=[b'opaque-signature-bytes'],
        ))
        response = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Do it'}], 'sys', None))

        messages = self.provider.append_turn([{'role': 'user', 'content': 'Do it'}], response, tool_results=[('call_1', 'ok')])

        self.assertEqual(messages[-2]['parts'][0]['function_call']['thought_signature'], b'opaque-signature-bytes')

    def test_synthetic_ids_stay_unique_across_multiple_rounds_of_the_same_tool(self):
        # Regression test: the real API error was "Function call is missing a
        # thought_signature... position 2" — caused by two separate rounds of
        # calling the same tool getting the SAME synthetic id (both "_0" since
        # each response's local part index restarted from 0), which silently
        # overwrote the first round's stored signature with the second's.
        self.provider.client.aio.models.generate_content = AsyncMock(
            return_value=_make_response(
                function_calls=[_FunctionCall(None, 'generate_image', {'prompt': 'a cat'})],
                thought_signatures=[b'signature-round-1'],
            )
        )
        response1 = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Do it'}], 'sys', None))

        self.provider.client.aio.models.generate_content = AsyncMock(
            return_value=_make_response(
                function_calls=[_FunctionCall(None, 'generate_image', {'prompt': 'a dog'})],
                thought_signatures=[b'signature-round-2'],
            )
        )
        response2 = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Do it'}], 'sys', None))

        id_1 = response1.tool_calls[0].id
        id_2 = response2.tool_calls[0].id
        self.assertNotEqual(id_1, id_2)
        self.assertEqual(self.provider._thought_signatures[id_1], b'signature-round-1')
        self.assertEqual(self.provider._thought_signatures[id_2], b'signature-round-2')

    def test_append_turn_builds_model_and_function_response_messages(self):
        response = ProviderResponse(
            text='', tool_calls=[ToolCall(id='call_1', name='my_tool', arguments={'x': 1})],
        )
        messages = self.provider.append_turn(
            [{'role': 'user', 'content': 'Go'}], response, tool_results=[('call_1', 'result')],
        )
        self.assertEqual(messages[-2], {
            'role': 'model',
            'parts': [{'function_call': {'name': 'my_tool', 'args': {'x': 1}, 'id': 'call_1', 'thought_signature': None}}],
        })
        self.assertEqual(messages[-1], {'role': 'user', 'parts': [{'function_response': {'name': 'my_tool', 'response': {'result': 'result'}, 'id': 'call_1'}}]})


class GeminiProviderStreamTest(SimpleTestCase):
    def setUp(self):
        with patch('ai_providers.google.google_provider.genai.Client'):
            self.provider = GeminiProvider()
        self.assistant = MagicMock()
        self.assistant.model = 'gemini-2.5-flash'
        self.assistant.instructions = 'Be helpful.'

    async def _collect(self, agen):
        return [chunk async for chunk in agen]

    def test_stream_accumulates_text_and_usage(self):
        chunks = [
            _make_response(text='Hel'),
            _make_response(text='lo!', prompt_tokens=8, candidate_tokens=3),
        ]
        self.provider.client.aio.models.generate_content_stream = AsyncMock(return_value=_FakeStream(chunks))
        usage = UsageAccumulator()

        result = run(self._collect(self.provider.stream(
            self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None, tool_executor=None, usage=usage,
        )))

        self.assertEqual(''.join(result), 'Hello!')
        self.assertEqual(usage.input_tokens, 8)
        self.assertEqual(usage.output_tokens, 3)

    def test_stream_runs_agent_loop_when_tool_calls_present(self):
        chunks = [
            _make_response(text=None, function_calls=[_FunctionCall('call_1', 'get_data', {'a': 1})]),
        ]
        self.provider.client.aio.models.generate_content_stream = AsyncMock(return_value=_FakeStream(chunks))

        async def tool_executor(name, arguments):
            self.assertEqual(name, 'get_data')
            self.assertEqual(arguments, {'a': 1})
            return 'tool result'

        with patch.object(self.provider, 'complete', new=AsyncMock(return_value=ProviderResponse(text='done', tool_calls=[]))):
            result = run(self._collect(self.provider.stream(
                self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None, tool_executor=tool_executor,
            )))

        self.assertEqual(result[-1], 'done')

    def test_stream_captures_thought_signature(self):
        chunks = [
            _make_response(
                text=None,
                function_calls=[_FunctionCall('call_1', 'generate_image', {'prompt': 'a cat'})],
                thought_signatures=[b'sig-from-stream'],
            ),
        ]
        self.provider.client.aio.models.generate_content_stream = AsyncMock(return_value=_FakeStream(chunks))

        async def tool_executor(name, arguments):
            return 'tool result'

        with patch.object(self.provider, 'complete', new=AsyncMock(return_value=ProviderResponse(text='done', tool_calls=[]))):
            run(self._collect(self.provider.stream(
                self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None, tool_executor=tool_executor,
            )))

        self.assertEqual(self.provider._thought_signatures.get('call_1'), b'sig-from-stream')
