import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from ai_providers.anthropic.anthropic_provider import AnthropicProvider


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class AnthropicProviderChatTest(SimpleTestCase):
    def setUp(self):
        with patch('ai_providers.anthropic.anthropic_provider.anthropic.AsyncAnthropic'):
            self.provider = AnthropicProvider()
        self.assistant = MagicMock()
        self.assistant.model = 'claude-sonnet-4-6'
        self.assistant.instructions = 'Be helpful.'

    def _make_text_response(self, text: str, stop_reason: str = 'end_turn'):
        block = MagicMock()
        block.type = 'text'
        block.text = text
        response = MagicMock()
        response.stop_reason = stop_reason
        response.content = [block]
        return response

    def test_returns_text_from_response(self):
        self.provider.client.messages.create = AsyncMock(
            return_value=self._make_text_response('Hello!')
        )
        result = run(self.provider.chat(
            self.assistant, [{'role': 'user', 'content': 'Hi'}], system='Be helpful.'
        ))
        self.assertEqual(result, 'Hello!')

    def test_does_not_include_tools_param_when_no_tools(self):
        self.provider.client.messages.create = AsyncMock(
            return_value=self._make_text_response('OK')
        )
        run(self.provider.chat(
            self.assistant, [{'role': 'user', 'content': 'Hi'}], system='Be helpful.'
        ))
        call_kwargs = self.provider.client.messages.create.call_args.kwargs
        self.assertNotIn('tools', call_kwargs)

    def test_includes_tools_param_when_tools_provided(self):
        self.provider.client.messages.create = AsyncMock(
            return_value=self._make_text_response('OK')
        )
        tools = [{'name': 'my_tool', 'description': '...', 'input_schema': {}}]
        run(self.provider.chat(
            self.assistant, [{'role': 'user', 'content': 'Hi'}],
            system='Be helpful.', tools=tools,
        ))
        call_kwargs = self.provider.client.messages.create.call_args.kwargs
        self.assertIn('tools', call_kwargs)
        self.assertEqual(call_kwargs['tools'], tools)

    def test_executes_tool_use_loop_and_returns_final_text(self):
        tool_block = MagicMock()
        tool_block.type = 'tool_use'
        tool_block.id = 'toolu_abc'
        tool_block.name = 'get_data'
        tool_block.input = {'param': 'value'}

        tool_response = MagicMock()
        tool_response.stop_reason = 'tool_use'
        tool_response.content = [tool_block]

        final_response = self._make_text_response('Done using the tool.')

        self.provider.client.messages.create = AsyncMock(
            side_effect=[tool_response, final_response]
        )
        mock_executor = AsyncMock(return_value='tool result data')
        tools = [{'name': 'get_data', 'description': '...', 'input_schema': {}}]

        result = run(self.provider.chat(
            self.assistant,
            [{'role': 'user', 'content': 'Do it'}],
            system='Be helpful.',
            tools=tools,
            tool_executor=mock_executor,
        ))

        self.assertEqual(result, 'Done using the tool.')
        mock_executor.assert_called_once_with('get_data', {'param': 'value'})
        self.assertEqual(self.provider.client.messages.create.call_count, 2)

    def test_tool_result_appended_to_messages_on_second_call(self):
        tool_block = MagicMock()
        tool_block.type = 'tool_use'
        tool_block.id = 'toolu_xyz'
        tool_block.name = 'my_tool'
        tool_block.input = {}

        tool_response = MagicMock()
        tool_response.stop_reason = 'tool_use'
        tool_response.content = [tool_block]

        final_response = self._make_text_response('Final.')

        self.provider.client.messages.create = AsyncMock(
            side_effect=[tool_response, final_response]
        )
        mock_executor = AsyncMock(return_value='result')

        run(self.provider.chat(
            self.assistant,
            [{'role': 'user', 'content': 'Go'}],
            system='sys',
            tools=[{'name': 'my_tool', 'description': '', 'input_schema': {}}],
            tool_executor=mock_executor,
        ))

        second_call_kwargs = self.provider.client.messages.create.call_args_list[1].kwargs
        messages = second_call_kwargs['messages']
        self.assertEqual(messages[-1]['role'], 'user')
        tool_result = messages[-1]['content'][0]
        self.assertEqual(tool_result['type'], 'tool_result')
        self.assertEqual(tool_result['tool_use_id'], 'toolu_xyz')
        self.assertEqual(tool_result['content'], 'result')
