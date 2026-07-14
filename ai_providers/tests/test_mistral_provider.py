import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from ai_providers.mistral.mistral_provider import MistralProvider
from ai_providers.base import ProviderResponse, ToolCall, UsageAccumulator


def run(coro):
    return asyncio.run(coro)


class _FakeEventStream:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._agen()

    async def _agen(self):
        for event in self._events:
            yield event


def _make_event(content=None, tool_call_deltas=None, usage=None, finish_reason=None):
    delta = MagicMock(content=content, tool_calls=tool_call_deltas)
    choice = MagicMock(delta=delta, finish_reason=finish_reason)
    chunk = MagicMock(choices=[choice], usage=usage)
    return MagicMock(data=chunk)


def _make_tool_call_delta(index, call_id=None, name=None, arguments=None):
    function = MagicMock(name=None, arguments=arguments)
    function.name = name
    return MagicMock(index=index, id=call_id, function=function)


class MistralProviderCompleteTest(SimpleTestCase):
    def setUp(self):
        with patch('ai_providers.mistral.mistral_provider.Mistral'):
            self.provider = MistralProvider()
        self.assistant = MagicMock()
        self.assistant.model = 'mistral-large-latest'
        self.assistant.instructions = 'Be helpful.'

    def _make_text_response(self, text: str, prompt_tokens=10, completion_tokens=5, finish_reason='stop'):
        message = MagicMock(content=text, tool_calls=None)
        choice = MagicMock(message=message, finish_reason=finish_reason)
        usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        return MagicMock(choices=[choice], usage=usage)

    def _make_tool_call_response(self, call_id, name, arguments, prompt_tokens=10, completion_tokens=5):
        function = MagicMock(name=None, arguments=arguments)
        function.name = name
        tool_call = MagicMock(id=call_id, function=function)
        message = MagicMock(content=None, tool_calls=[tool_call])
        choice = MagicMock(message=message, finish_reason='tool_calls')
        usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        return MagicMock(choices=[choice], usage=usage)

    def test_complete_returns_text_response(self):
        self.provider.client.chat.complete_async = AsyncMock(return_value=self._make_text_response('Hello!'))
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'Be helpful.', None))
        self.assertEqual(result.text, 'Hello!')
        self.assertFalse(result.requires_tool_execution)

    def test_complete_translates_tools_param(self):
        self.provider.client.chat.complete_async = AsyncMock(return_value=self._make_text_response('OK'))
        tools = [{'name': 'my_tool', 'description': 'does a thing', 'input_schema': {'type': 'object'}}]
        run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', tools))
        call_kwargs = self.provider.client.chat.complete_async.call_args.kwargs
        self.assertEqual(call_kwargs['tools'], [
            {'type': 'function', 'function': {'name': 'my_tool', 'description': 'does a thing', 'parameters': {'type': 'object'}}}
        ])

    def test_complete_extracts_tool_calls_with_dict_arguments(self):
        self.provider.client.chat.complete_async = AsyncMock(
            return_value=self._make_tool_call_response('call_abc', 'get_data', {'param': 'value'})
        )
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Do it'}], 'sys', None))
        self.assertEqual(result.tool_calls, [ToolCall(id='call_abc', name='get_data', arguments={'param': 'value'})])

    def test_complete_extracts_tool_calls_with_string_arguments(self):
        self.provider.client.chat.complete_async = AsyncMock(
            return_value=self._make_tool_call_response('call_abc', 'get_data', json.dumps({'param': 'value'}))
        )
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Do it'}], 'sys', None))
        self.assertEqual(result.tool_calls, [ToolCall(id='call_abc', name='get_data', arguments={'param': 'value'})])

    def test_complete_captures_usage(self):
        self.provider.client.chat.complete_async = AsyncMock(
            return_value=self._make_text_response('Hi', prompt_tokens=42, completion_tokens=17)
        )
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None))
        self.assertEqual(result.usage, {'input_tokens': 42, 'output_tokens': 17})

    def test_complete_captures_finish_reason(self):
        self.provider.client.chat.complete_async = AsyncMock(
            return_value=self._make_text_response('Cut off...', finish_reason='length')
        )
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None))
        self.assertEqual(result.finish_reason, 'length')

    def test_append_turn_builds_tool_message(self):
        raw = self._make_tool_call_response('call_xyz', 'my_tool', {})
        response = ProviderResponse(text='', tool_calls=[ToolCall(id='call_xyz', name='my_tool', arguments={})], raw=raw)

        messages = self.provider.append_turn(
            [{'role': 'user', 'content': 'Go'}], response, tool_results=[('call_xyz', 'result')],
        )

        self.assertEqual(messages[-2]['role'], 'assistant')
        self.assertEqual(messages[-2]['tool_calls'][0]['id'], 'call_xyz')
        self.assertEqual(messages[-1], {'role': 'tool', 'tool_call_id': 'call_xyz', 'content': 'result'})


class MistralProviderStreamTest(SimpleTestCase):
    def setUp(self):
        with patch('ai_providers.mistral.mistral_provider.Mistral'):
            self.provider = MistralProvider()
        self.assistant = MagicMock()
        self.assistant.model = 'mistral-large-latest'
        self.assistant.instructions = 'Be helpful.'

    async def _collect(self, agen):
        return [chunk async for chunk in agen]

    def test_stream_accumulates_text_and_usage(self):
        events = [
            _make_event(content='Hel'),
            _make_event(content='lo!'),
            _make_event(content=None, usage=MagicMock(prompt_tokens=8, completion_tokens=3), finish_reason='stop'),
        ]
        self.provider.client.chat.stream_async = AsyncMock(return_value=_FakeEventStream(events))
        usage = UsageAccumulator()

        result = run(self._collect(self.provider.stream(
            self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None, tool_executor=None, usage=usage,
        )))

        self.assertEqual(''.join(result), 'Hello!')
        self.assertEqual(usage.input_tokens, 8)
        self.assertEqual(usage.output_tokens, 3)

    def test_stream_warns_on_suspicious_finish_reason_with_no_tool_use(self):
        # Regression test: a plain streamed reply with no tool call never
        # reaches run_agent_loop (which does its own check) — this is the
        # only place such a response's finish_reason is ever inspected.
        events = [_make_event(content='Cut off', finish_reason='length')]
        self.provider.client.chat.stream_async = AsyncMock(return_value=_FakeEventStream(events))

        with self.assertLogs('ai_providers.base', level='WARNING'):
            run(self._collect(self.provider.stream(
                self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None, tool_executor=None,
            )))

    def test_stream_accumulates_fragmented_tool_call_and_runs_agent_loop(self):
        events = [
            _make_event(tool_call_deltas=[_make_tool_call_delta(0, call_id='call_1', name='get_')]),
            _make_event(tool_call_deltas=[_make_tool_call_delta(0, name='data', arguments='{"a"')]),
            _make_event(tool_call_deltas=[_make_tool_call_delta(0, arguments=': 1}')], finish_reason='tool_calls'),
        ]
        self.provider.client.chat.stream_async = AsyncMock(return_value=_FakeEventStream(events))

        async def tool_executor(name, arguments):
            self.assertEqual(name, 'get_data')
            self.assertEqual(arguments, {'a': 1})
            return 'tool result'

        with patch.object(self.provider, 'complete', new=AsyncMock(return_value=ProviderResponse(text='done', tool_calls=[]))):
            result = run(self._collect(self.provider.stream(
                self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None, tool_executor=tool_executor,
            )))

        self.assertEqual(result[-1], 'done')
