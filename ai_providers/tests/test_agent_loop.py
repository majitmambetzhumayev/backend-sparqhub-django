import asyncio
from unittest.mock import AsyncMock, MagicMock, call

from django.test import SimpleTestCase

from ai_providers.agent_loop import MAX_TOOL_ITERATIONS, run_agent_loop
from ai_providers.base import ProviderResponse, ToolCall, UsageAccumulator


def run(coro):
    return asyncio.run(coro)


class RunAgentLoopTest(SimpleTestCase):
    def test_loops_until_no_more_tool_calls(self):
        tool_response = ProviderResponse(text='', tool_calls=[ToolCall(id='1', name='search', arguments={'q': 'x'})])
        final_response = ProviderResponse(text='Done using the tool.', tool_calls=[])

        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=[tool_response, final_response])
        provider.append_turn = MagicMock(side_effect=lambda messages, response, tool_results=None: [
            *messages, {'role': 'user', 'content': str(tool_results)},
        ])
        tool_executor = AsyncMock(return_value='tool result data')

        result = run(run_agent_loop(
            provider, MagicMock(), [{'role': 'user', 'content': 'Do it'}], 'sys', [], tool_executor,
        ))

        self.assertEqual(result, 'Done using the tool.')
        tool_executor.assert_called_once_with('search', {'q': 'x'})
        self.assertEqual(provider.complete.call_count, 2)

    def test_returns_text_immediately_when_no_tool_calls(self):
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=ProviderResponse(text='Hi', tool_calls=[]))

        result = run(run_agent_loop(provider, MagicMock(), [], 'sys', [], None))

        self.assertEqual(result, 'Hi')
        provider.append_turn.assert_not_called()

    def test_uses_initial_response_without_extra_complete_call(self):
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=ProviderResponse(text='Follow-up', tool_calls=[]))
        provider.append_turn = MagicMock(return_value=[])
        initial_response = ProviderResponse(text='', tool_calls=[ToolCall(id='1', name='x', arguments={})])
        tool_executor = AsyncMock(return_value='r')

        result = run(run_agent_loop(
            provider, MagicMock(), [], 'sys', [], tool_executor, initial_response=initial_response,
        ))

        self.assertEqual(result, 'Follow-up')
        self.assertEqual(provider.complete.call_count, 1)

    def test_calls_on_tool_call_before_each_tool_execution(self):
        tool_response = ProviderResponse(text='', tool_calls=[
            ToolCall(id='1', name='search', arguments={'q': 'x'}),
            ToolCall(id='2', name='lookup', arguments={'k': 'y'}),
        ])
        final_response = ProviderResponse(text='Done.', tool_calls=[])
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=[tool_response, final_response])
        provider.append_turn = MagicMock(return_value=[])
        tool_executor = AsyncMock(return_value='result')
        on_tool_call = AsyncMock()

        result = run(run_agent_loop(
            provider, MagicMock(), [], 'sys', [], tool_executor, on_tool_call=on_tool_call,
        ))

        self.assertEqual(result, 'Done.')
        self.assertEqual(on_tool_call.await_args_list, [call('search'), call('lookup')])

    def test_accumulates_usage_across_tool_loop_iterations(self):
        tool_response = ProviderResponse(
            text='', tool_calls=[ToolCall(id='1', name='search', arguments={'q': 'x'})],
            usage={'input_tokens': 100, 'output_tokens': 20},
        )
        final_response = ProviderResponse(
            text='Done.', tool_calls=[], usage={'input_tokens': 150, 'output_tokens': 30},
        )
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=[tool_response, final_response])
        provider.append_turn = MagicMock(return_value=[])
        tool_executor = AsyncMock(return_value='tool result')
        usage = UsageAccumulator()

        run(run_agent_loop(provider, MagicMock(), [], 'sys', [], tool_executor, usage=usage))

        self.assertEqual(usage.input_tokens, 250)
        self.assertEqual(usage.output_tokens, 50)

    def test_stops_after_max_tool_iterations_instead_of_looping_forever(self):
        # A model that never converges (keeps requesting tools every round)
        # must not hang the turn indefinitely.
        never_converges = ProviderResponse(text='', tool_calls=[ToolCall(id='1', name='search', arguments={})])
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=never_converges)
        provider.append_turn = MagicMock(return_value=[])
        tool_executor = AsyncMock(return_value='result')

        result = run(run_agent_loop(provider, MagicMock(), [], 'sys', [], tool_executor))

        self.assertEqual(provider.complete.call_count, MAX_TOOL_ITERATIONS + 1)
        self.assertIn("wasn't able to finish", result)
