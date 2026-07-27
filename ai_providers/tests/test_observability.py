import asyncio
from unittest.mock import AsyncMock, MagicMock

from django.test import SimpleTestCase
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import ai_providers.observability as observability
from ai_providers.agent_loop import run_agent_loop
from ai_providers.base import ProviderResponse, ToolCall, UsageAccumulator
from ai_providers.chat_router import _stream_and_release


def run(coro):
    return asyncio.run(coro)


class ObservabilitySpanTest(SimpleTestCase):
    # A real (in-memory, not console/OTLP) tracer is installed here rather
    # than mocking observability.py's helpers -- the point is to verify the
    # actual gen_ai.* attributes a real OTel backend would see, not just
    # that some function got called. observability._tracer_instance (not
    # the OTel *global* provider) is what's swapped: the global provider is
    # write-once by design, so a second trace.set_tracer_provider() call in
    # a test would be silently ignored -- see observability.py's own comment.
    def setUp(self):
        self.exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self._previous_tracer = observability._tracer_instance
        observability._tracer_instance = provider.get_tracer("test")

    def tearDown(self):
        observability._tracer_instance = self._previous_tracer

    def test_llm_call_span_carries_gen_ai_attributes(self):
        provider = MagicMock()
        provider.label = 'Anthropic'
        provider.complete = AsyncMock(
            return_value=ProviderResponse(text='Hi', tool_calls=[], usage={'input_tokens': 12, 'output_tokens': 7}),
        )
        assistant = MagicMock(model='claude-sonnet-5')

        run(run_agent_loop(provider, assistant, [], 'sys', [], None))

        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.name, 'chat claude-sonnet-5')
        self.assertEqual(span.attributes['gen_ai.operation.name'], 'chat')
        self.assertEqual(span.attributes['gen_ai.provider.name'], 'anthropic')
        self.assertEqual(span.attributes['gen_ai.request.model'], 'claude-sonnet-5')
        self.assertEqual(span.attributes['gen_ai.response.model'], 'claude-sonnet-5')
        self.assertEqual(span.attributes['gen_ai.usage.input_tokens'], 12)
        self.assertEqual(span.attributes['gen_ai.usage.output_tokens'], 7)

    def test_tool_call_span_carries_gen_ai_attributes(self):
        provider = MagicMock()
        provider.label = 'Anthropic'
        tool_response = ProviderResponse(text='', tool_calls=[ToolCall(id='1', name='search_project_files', arguments={})])
        final_response = ProviderResponse(text='Done.', tool_calls=[])
        provider.complete = AsyncMock(side_effect=[tool_response, final_response])
        provider.append_turn = MagicMock(return_value=[])
        tool_executor = AsyncMock(return_value='result')
        assistant = MagicMock(model='claude-sonnet-5')

        run(run_agent_loop(provider, assistant, [], 'sys', [], tool_executor))

        tool_spans = [s for s in self.exporter.get_finished_spans() if s.name.startswith('execute_tool')]
        self.assertEqual(len(tool_spans), 1)
        self.assertEqual(tool_spans[0].name, 'execute_tool search_project_files')
        self.assertEqual(tool_spans[0].attributes['gen_ai.operation.name'], 'execute_tool')
        self.assertEqual(tool_spans[0].attributes['gen_ai.tool.name'], 'search_project_files')

    def test_each_tool_iteration_gets_its_own_llm_span(self):
        provider = MagicMock()
        provider.label = 'Anthropic'
        tool_response = ProviderResponse(
            text='', tool_calls=[ToolCall(id='1', name='search', arguments={})],
            usage={'input_tokens': 10, 'output_tokens': 5},
        )
        final_response = ProviderResponse(text='Done.', tool_calls=[], usage={'input_tokens': 20, 'output_tokens': 8})
        provider.complete = AsyncMock(side_effect=[tool_response, final_response])
        provider.append_turn = MagicMock(return_value=[])
        tool_executor = AsyncMock(return_value='result')
        assistant = MagicMock(model='claude-sonnet-5')

        run(run_agent_loop(provider, assistant, [], 'sys', [], tool_executor))

        llm_spans = [s for s in self.exporter.get_finished_spans() if s.name.startswith('chat')]
        self.assertEqual(len(llm_spans), 2)
        self.assertEqual(
            [s.attributes['gen_ai.usage.input_tokens'] for s in llm_spans],
            [10, 20],
        )

    def test_streaming_turn_gets_a_span_even_with_no_tool_calls(self):
        # The one case run_agent_loop never sees at all: a streamed turn
        # with no tool calls never enters it (see _stream_and_release's own
        # docstring) -- this is the only place that initial call gets
        # spanned.
        provider = MagicMock()
        provider.label = 'Anthropic'
        provider.aclose = AsyncMock()
        usage = UsageAccumulator(input_tokens=15, output_tokens=9)

        async def fake_chunks():
            yield 'Hel'
            yield 'lo!'

        async def drain():
            return [c async for c in _stream_and_release(provider, fake_chunks(), 'claude-sonnet-5', usage)]

        chunks = run(drain())

        self.assertEqual(chunks, ['Hel', 'lo!'])
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].name, 'chat claude-sonnet-5')
        self.assertEqual(spans[0].attributes['gen_ai.provider.name'], 'anthropic')
        self.assertEqual(spans[0].attributes['gen_ai.usage.input_tokens'], 15)
        self.assertEqual(spans[0].attributes['gen_ai.usage.output_tokens'], 9)
