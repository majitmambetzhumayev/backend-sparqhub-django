import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from ai_providers.openai.openai_provider import OpenAIProvider
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


def _make_chunk(content=None, tool_call_deltas=None, usage=None):
    delta = MagicMock(content=content, tool_calls=tool_call_deltas)
    choice = MagicMock(delta=delta)
    return MagicMock(choices=[choice], usage=usage)


def _make_tool_call_delta(index, call_id=None, name=None, arguments=None):
    function = MagicMock(name=None, arguments=arguments)
    function.name = name
    return MagicMock(index=index, id=call_id, function=function)


class OpenAIProviderCompleteTest(SimpleTestCase):
    def setUp(self):
        with patch('ai_providers.openai.openai_provider.AsyncOpenAI'):
            self.provider = OpenAIProvider()
        self.assistant = MagicMock()
        self.assistant.model = 'gpt-5.4'
        self.assistant.instructions = 'Be helpful.'

    def _make_text_response(self, text: str, prompt_tokens=10, completion_tokens=5):
        message = MagicMock(content=text, tool_calls=None)
        choice = MagicMock(message=message)
        usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        return MagicMock(choices=[choice], usage=usage)

    def _make_tool_call_response(self, call_id, name, arguments, prompt_tokens=10, completion_tokens=5):
        function = MagicMock(name=None, arguments=json.dumps(arguments))
        function.name = name
        tool_call = MagicMock(id=call_id, function=function)
        message = MagicMock(content=None, tool_calls=[tool_call])
        choice = MagicMock(message=message)
        usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        return MagicMock(choices=[choice], usage=usage)

    def test_complete_returns_text_response(self):
        self.provider.client.chat.completions.create = AsyncMock(return_value=self._make_text_response('Hello!'))
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'Be helpful.', None))
        self.assertEqual(result.text, 'Hello!')
        self.assertFalse(result.requires_tool_execution)

    def test_complete_prepends_system_message(self):
        self.provider.client.chat.completions.create = AsyncMock(return_value=self._make_text_response('OK'))
        run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys prompt', None))
        call_kwargs = self.provider.client.chat.completions.create.call_args.kwargs
        self.assertEqual(call_kwargs['messages'][0], {'role': 'system', 'content': 'sys prompt'})

    def test_complete_does_not_include_tools_param_when_no_tools(self):
        self.provider.client.chat.completions.create = AsyncMock(return_value=self._make_text_response('OK'))
        run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None))
        call_kwargs = self.provider.client.chat.completions.create.call_args.kwargs
        self.assertNotIn('tools', call_kwargs)

    def test_complete_translates_tools_param(self):
        self.provider.client.chat.completions.create = AsyncMock(return_value=self._make_text_response('OK'))
        tools = [{'name': 'my_tool', 'description': 'does a thing', 'input_schema': {'type': 'object'}}]
        run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', tools))
        call_kwargs = self.provider.client.chat.completions.create.call_args.kwargs
        self.assertEqual(call_kwargs['tools'], [
            {'type': 'function', 'function': {'name': 'my_tool', 'description': 'does a thing', 'parameters': {'type': 'object'}}}
        ])

    def test_complete_extracts_tool_calls(self):
        self.provider.client.chat.completions.create = AsyncMock(
            return_value=self._make_tool_call_response('call_abc', 'get_data', {'param': 'value'})
        )
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Do it'}], 'sys', None))
        self.assertTrue(result.requires_tool_execution)
        self.assertEqual(result.tool_calls, [ToolCall(id='call_abc', name='get_data', arguments={'param': 'value'})])

    def test_complete_captures_usage(self):
        self.provider.client.chat.completions.create = AsyncMock(
            return_value=self._make_text_response('Hi', prompt_tokens=42, completion_tokens=17)
        )
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None))
        self.assertEqual(result.usage, {'input_tokens': 42, 'output_tokens': 17})

    def test_append_turn_builds_tool_message(self):
        raw = self._make_tool_call_response('call_xyz', 'my_tool', {})
        response = ProviderResponse(text='', tool_calls=[ToolCall(id='call_xyz', name='my_tool', arguments={})], raw=raw)

        messages = self.provider.append_turn(
            [{'role': 'user', 'content': 'Go'}], response, tool_results=[('call_xyz', 'result')],
        )

        self.assertEqual(messages[-2]['role'], 'assistant')
        self.assertEqual(messages[-2]['tool_calls'][0]['id'], 'call_xyz')
        self.assertEqual(messages[-1], {'role': 'tool', 'tool_call_id': 'call_xyz', 'content': 'result'})


class OpenAIProviderStreamTest(SimpleTestCase):
    def setUp(self):
        with patch('ai_providers.openai.openai_provider.AsyncOpenAI'):
            self.provider = OpenAIProvider()
        self.assistant = MagicMock()
        self.assistant.model = 'gpt-5.4'
        self.assistant.instructions = 'Be helpful.'

    async def _collect(self, agen):
        return [chunk async for chunk in agen]

    def test_stream_accumulates_text_and_usage(self):
        chunks = [
            _make_chunk(content='Hel'),
            _make_chunk(content='lo!'),
            _make_chunk(content=None, usage=MagicMock(prompt_tokens=8, completion_tokens=3)),
        ]
        self.provider.client.chat.completions.create = AsyncMock(return_value=_FakeStream(chunks))
        usage = UsageAccumulator()

        result = run(self._collect(self.provider.stream(
            self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None, tool_executor=None, usage=usage,
        )))

        self.assertEqual(''.join(result), 'Hello!')
        self.assertEqual(usage.input_tokens, 8)
        self.assertEqual(usage.output_tokens, 3)

    def test_stream_accumulates_fragmented_tool_call_and_runs_agent_loop(self):
        chunks = [
            _make_chunk(tool_call_deltas=[_make_tool_call_delta(0, call_id='call_1', name='get_')]),
            _make_chunk(tool_call_deltas=[_make_tool_call_delta(0, name='data', arguments='{"a"')]),
            _make_chunk(tool_call_deltas=[_make_tool_call_delta(0, arguments=': 1}')]),
            _make_chunk(usage=MagicMock(prompt_tokens=5, completion_tokens=2)),
        ]
        self.provider.client.chat.completions.create = AsyncMock(return_value=_FakeStream(chunks))

        async def tool_executor(name, arguments):
            self.assertEqual(name, 'get_data')
            self.assertEqual(arguments, {'a': 1})
            return 'tool result'

        with patch.object(self.provider, 'complete', new=AsyncMock(return_value=ProviderResponse(text='done', tool_calls=[]))):
            result = run(self._collect(self.provider.stream(
                self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None, tool_executor=tool_executor,
            )))

        self.assertEqual(result[-1], 'done')
