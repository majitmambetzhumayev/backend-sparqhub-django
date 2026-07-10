import logging

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
    if initial_response is not None:
        response = initial_response
    else:
        response = await provider.complete(assistant, messages, system, tools)
        if usage is not None and response.usage:
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
            results.append((call.id, await tool_executor(call.name, call.arguments)))
        messages = provider.append_turn(messages, response, tool_results=results)
        response = await provider.complete(assistant, messages, system, tools)
        if usage is not None and response.usage:
            usage.add(**response.usage)
    return response.text
