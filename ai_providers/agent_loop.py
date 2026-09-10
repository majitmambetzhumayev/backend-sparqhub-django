import logging

from ai_providers.base import warn_if_finish_reason_suspicious
from ai_providers.observability import llm_call_span, record_llm_usage, tool_call_span

logger = logging.getLogger(__name__)

# A model that keeps requesting tools without ever converging (e.g. asked to
# inspect an uploaded image through a text-only search tool, and never gives
# up) would otherwise loop here forever — nothing else in a turn's lifecycle
# bounds tool-call rounds specifically (CONFIRMATION_TIMEOUT_SECONDS in
# chat_messages/services.py only bounds a pending user confirmation).
MAX_TOOL_ITERATIONS = 15


async def run_agent_loop(
    provider, assistant, messages, system, tools, tool_executor,
    initial_response=None, usage=None, on_tool_call=None,
) -> str:
    """
    The one tool-use loop shared by every provider: call the model, and while
    it keeps asking for tools, execute them and call again. Providers only
    need to implement `complete()`/`append_turn()` — this loop is written once.

    `usage`, when passed, is an ai_providers.base.UsageAccumulator that gets
    `.add()`-ed after every `complete()` call so a tool-heavy turn's total
    cost is captured, not just the last call.

    `on_tool_call`, when passed, is an async callback invoked with a tool's
    name right before it executes — lets callers (e.g. the WS consumer)
    surface "using tool X" status to the user during an otherwise-silent gap.
    """
    provider_name = provider.label.lower()

    if initial_response is not None:
        response = initial_response
    else:
        with llm_call_span(provider_name, assistant.model) as span:
            response = await provider.complete(assistant, messages, system, tools)
            # Unconditional now (previously gated behind `if response.usage`,
            # which also silently skipped recording finish_reason whenever a
            # provider call carried no usage data) -- record_llm_usage
            # already no-ops per field when a value is None.
            record_llm_usage(
                span, response_model=assistant.model,
                input_tokens=response.usage.get("input_tokens") if response.usage else None,
                output_tokens=response.usage.get("output_tokens") if response.usage else None,
                finish_reason=response.finish_reason,
            )
            if response.usage and usage is not None:
                usage.add(**response.usage)
    iterations = 0
    while response.requires_tool_execution and tool_executor:
        iterations += 1
        if iterations > MAX_TOOL_ITERATIONS:
            logger.warning("Agent loop exceeded %s tool-call iterations, stopping", MAX_TOOL_ITERATIONS)
            return "I wasn't able to finish this after several tool calls — could you rephrase or narrow your request?"
        results = []
        for call in response.tool_calls:
            if on_tool_call is not None:
                await on_tool_call(call.name)
            with tool_call_span(call.name):
                results.append((call.id, await tool_executor(call.name, call.arguments)))
        messages = provider.append_turn(messages, response, tool_results=results)
        with llm_call_span(provider_name, assistant.model) as span:
            response = await provider.complete(assistant, messages, system, tools)
            record_llm_usage(
                span, response_model=assistant.model,
                input_tokens=response.usage.get("input_tokens") if response.usage else None,
                output_tokens=response.usage.get("output_tokens") if response.usage else None,
                finish_reason=response.finish_reason,
            )
            if response.usage and usage is not None:
                usage.add(**response.usage)
    warn_if_finish_reason_suspicious(response)
    return response.text
