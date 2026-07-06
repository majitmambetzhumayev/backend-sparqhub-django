import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from ai_providers.anthropic.anthropic_provider import AnthropicProvider
from ai_providers.base import ProviderResponse, ToolCall


def run(coro):
    return asyncio.run(coro)


class AnthropicProviderCompleteTest(SimpleTestCase):
    def setUp(self):
        with patch('ai_providers.anthropic.anthropic_provider.anthropic.AsyncAnthropic'):
            self.provider = AnthropicProvider()
        self.assistant = MagicMock()
        self.assistant.model = 'claude-sonnet-4-6'
        self.assistant.instructions = 'Be helpful.'

    def _make_text_response(self, text: str, input_tokens=10, output_tokens=5):
        block = MagicMock()
        block.type = 'text'
        block.text = text
        response = MagicMock()
        response.content = [block]
        response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
        return response

    def _make_tool_use_response(self, call_id, name, arguments, input_tokens=10, output_tokens=5):
        block = MagicMock()
        block.type = 'tool_use'
        block.id = call_id
        block.name = name
        block.input = arguments
        response = MagicMock()
        response.content = [block]
        response.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
        return response

    def test_complete_returns_text_response(self):
        self.provider.client.messages.create = AsyncMock(return_value=self._make_text_response('Hello!'))
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'Be helpful.', None))
        self.assertEqual(result.text, 'Hello!')
        self.assertFalse(result.requires_tool_execution)

    def test_complete_does_not_include_tools_param_when_no_tools(self):
        self.provider.client.messages.create = AsyncMock(return_value=self._make_text_response('OK'))
        run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'Be helpful.', None))
        call_kwargs = self.provider.client.messages.create.call_args.kwargs
        self.assertNotIn('tools', call_kwargs)

    def test_complete_includes_tools_param_when_tools_provided(self):
        self.provider.client.messages.create = AsyncMock(return_value=self._make_text_response('OK'))
        tools = [{'name': 'my_tool', 'description': '...', 'input_schema': {}}]
        run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'Be helpful.', tools))
        call_kwargs = self.provider.client.messages.create.call_args.kwargs
        self.assertIn('tools', call_kwargs)
        self.assertEqual(call_kwargs['tools'], tools)

    def test_complete_extracts_tool_calls(self):
        self.provider.client.messages.create = AsyncMock(
            return_value=self._make_tool_use_response('toolu_abc', 'get_data', {'param': 'value'})
        )
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Do it'}], 'sys', None))
        self.assertTrue(result.requires_tool_execution)
        self.assertEqual(result.tool_calls, [ToolCall(id='toolu_abc', name='get_data', arguments={'param': 'value'})])

    def test_complete_captures_usage(self):
        self.provider.client.messages.create = AsyncMock(
            return_value=self._make_text_response('Hi', input_tokens=42, output_tokens=17)
        )
        result = run(self.provider.complete(self.assistant, [{'role': 'user', 'content': 'Hi'}], 'sys', None))
        self.assertEqual(result.usage, {'input_tokens': 42, 'output_tokens': 17})

    def test_append_turn_builds_tool_result_message(self):
        raw = self._make_tool_use_response('toolu_xyz', 'my_tool', {})
        response = ProviderResponse(text='', tool_calls=[ToolCall(id='toolu_xyz', name='my_tool', arguments={})], raw=raw)

        messages = self.provider.append_turn(
            [{'role': 'user', 'content': 'Go'}], response, tool_results=[('toolu_xyz', 'result')],
        )

        self.assertEqual(messages[-1]['role'], 'user')
        tool_result = messages[-1]['content'][0]
        self.assertEqual(tool_result['type'], 'tool_result')
        self.assertEqual(tool_result['tool_use_id'], 'toolu_xyz')
        self.assertEqual(tool_result['content'], 'result')
