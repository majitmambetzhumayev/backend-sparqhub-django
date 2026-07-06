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
    while response.requires_tool_execution and tool_executor:
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
