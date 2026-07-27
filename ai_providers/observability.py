# ai_providers/observability.py
"""Agent/LLM-call tracing via the OpenTelemetry GenAI semantic conventions
(https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/).

Two things live here: process-wide tracer setup (configure_tracing, called
once from settings.py) and the span-creation helpers used by
ai_providers/agent_loop.py and ai_providers/chat_router.py -- the two
places every chat turn's LLM calls and tool executions already pass
through (see CLAUDE.md's dependency-inversion note on chat_router.py being
the single call site), so instrumenting there covers every provider without
touching any of them individually.
"""
import sys
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

_TRACER_NAME = "sparqhub.ai_providers"

# A no-op tracer until configure_tracing() runs (matches the default
# global TracerProvider's own behavior: spans are created but go nowhere).
# Deliberately NOT stored via trace.set_tracer_provider()/get_tracer() --
# the OTel API treats the global provider as write-once (a second
# set_tracer_provider() call is silently ignored, logged as a warning),
# which makes swapping it out for a test-local provider impossible. Owning
# the reference directly here sidesteps that and is exactly what makes
# ObservabilitySpanTest able to point this at an in-memory exporter.
_tracer_instance = trace.get_tracer(_TRACER_NAME)


def configure_tracing(otlp_endpoint: str, service_name: str) -> None:
    global _tracer_instance
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    elif "test" not in sys.argv:
        # No collector configured -- console export so spans are still
        # visible locally without standing up new infra just to try this
        # out. Skipped specifically under `manage.py test`/CI (DEBUG=True
        # there too, so that alone isn't a usable signal): every mocked
        # provider call would otherwise dump a span to stdout on every test
        # run, pure noise with no collector reading it anyway.
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    _tracer_instance = provider.get_tracer(_TRACER_NAME)


def _tracer():
    return _tracer_instance


@contextmanager
def llm_call_span(provider_name: str, model: str):
    """Wraps one provider.complete()/stream() call. usage/response_model are
    set via the yielded span once the call returns, since neither is known
    up front -- see agent_loop.py's usage."""
    with _tracer().start_as_current_span(f"chat {model}") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.provider.name", provider_name)
        span.set_attribute("gen_ai.request.model", model)
        yield span


def record_llm_usage(span, *, response_model: str | None, input_tokens: int | None, output_tokens: int | None) -> None:
    if response_model:
        span.set_attribute("gen_ai.response.model", response_model)
    if input_tokens is not None:
        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    if output_tokens is not None:
        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)


@contextmanager
def tool_call_span(tool_name: str):
    with _tracer().start_as_current_span(f"execute_tool {tool_name}") as span:
        span.set_attribute("gen_ai.operation.name", "execute_tool")
        span.set_attribute("gen_ai.tool.name", tool_name)
        yield span
