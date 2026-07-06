import logging
import math
from types import SimpleNamespace

from asgiref.sync import sync_to_async

from ai_providers.agent_loop import run_agent_loop
from ai_providers.base import UsageAccumulator
from ai_providers.factory import get_provider, PROVIDERS

logger = logging.getLogger(__name__)

CREDIT_VALUE_USD = 0.01


class InsufficientCreditsError(Exception):
    pass


def _build_system_prompt(base: str, memories: list[str]) -> str:
    if not memories:
        return base
    context = "\n".join(f"- {m}" for m in memories)
    return f"{base}\n\nRelevant context from memory:\n{context}"


async def _get_mcp_context(project_id) -> tuple[list[dict], object]:
    from mcp_client.models import MCPServer
    from mcp_client.services import get_tools_from_server, call_tool

    if project_id is None:
        return [], None

    servers = [s async for s in MCPServer.objects.filter(project_id=project_id, enabled=True)]
    if not servers:
        return [], None

    all_tools: list[dict] = []
    tool_server_map: dict[str, object] = {}

    for server in servers:
        try:
            tools = await get_tools_from_server(server)
            all_tools.extend(tools)
            for tool in tools:
                tool_server_map[tool["name"]] = server
        except Exception:
            logger.warning("Failed to fetch tools from MCP server %s", server.name)

    if not all_tools:
        return [], None

    async def tool_executor(name: str, arguments: dict) -> str:
        server = tool_server_map.get(name)
        if server is None:
            raise ValueError(f"Unknown MCP tool: {name}")
        return await call_tool(server, name, arguments)

    return all_tools, tool_executor


IMAGE_GENERATION_TOOL = {
    "name": "generate_image",
    "description": "Generate an image from a text prompt and return a URL to the generated image.",
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "A detailed description of the image to generate."},
        },
        "required": ["prompt"],
    },
}


def _build_image_tool(ai_provider: str, api_key: str | None, user, used_global_key: bool):
    """Returns (tool_schema, executor) for the built-in generate_image tool, or
    (None, None) when the current chat provider has no matching image
    capability registered — image generation reuses the same provider (and
    BYOK key) as the current chat turn rather than a separately-chosen one."""
    from image_providers.factory import get_image_provider
    from image_providers.services import save_generated_image

    image_provider = get_image_provider(ai_provider, api_key=api_key)
    if image_provider is None:
        return None, None

    async def executor(arguments: dict) -> str:
        result = await image_provider.generate(arguments.get("prompt", ""))
        url = await sync_to_async(save_generated_image)(result.data, result.mime_type)
        if used_global_key:
            image_usage = UsageAccumulator(**result.usage)
            cost = _compute_cost_credits(type(image_provider), image_provider.MODEL, image_usage)
            if cost > 0:
                await sync_to_async(_apply_credit_deduction)(user, cost)
        return f"![Generated image]({url})"

    return IMAGE_GENERATION_TOOL, executor


DELEGATE_TOOL = {
    "name": "delegate_to_model",
    "description": (
        "Delegate this request to a different AI provider/model when you can't handle it yourself "
        "(e.g. you don't support image generation). Requires user confirmation before running. "
        "The other model's response is shown to the user and folded into this conversation — you "
        "remain the active model for the rest of the conversation afterward."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "provider": {"type": "string", "description": "The provider to delegate to.", "enum": list(PROVIDERS.keys())},
            "model": {
                "type": "string",
                "description": (
                    "Optional — a chat model id for the chosen provider. Omit it (recommended) to use "
                    "that provider's default chat model; an invalid or non-chat model id (e.g. an "
                    "image-generation model name) falls back to the default automatically."
                ),
            },
            "prompt": {"type": "string", "description": "The task/prompt to send to the other model."},
            "reason": {"type": "string", "description": "Briefly explain why you can't handle this yourself."},
        },
        "required": ["provider", "prompt", "reason"],
    },
}


def _build_delegate_tool(user, confirm_tool_call):
    """Returns (tool_schema, executor) for the built-in delegate_to_model tool —
    always offered, regardless of the current provider, since its whole point
    is escalating to a DIFFERENT provider. Gated behind confirm_tool_call (if
    given) so the user approves before the sub-call actually runs; the
    delegated call itself is just a fresh, one-shot send_chat_message with
    delegation disabled, so it can't recurse."""

    async def executor(arguments: dict) -> str:
        if confirm_tool_call is not None:
            confirmed = await confirm_tool_call("delegate_to_model", arguments)
            if not confirmed:
                return "The user declined this delegation. Continue the conversation yourself, or ask what they'd like instead."

        target_provider = arguments.get("provider", "")
        prompt = arguments.get("prompt", "")

        if target_provider not in PROVIDERS:
            return f"Unknown provider '{target_provider}'. Cannot delegate."

        # The calling model has no visibility into which model ids are actually
        # valid chat models for the target provider — it previously guessed
        # image-model ids (e.g. "gpt-image-1") that fail against the chat
        # completions endpoint. Validate against that provider's real model
        # list and fall back to its default instead of trusting the guess.
        available_models = [m["id"] for m in PROVIDERS[target_provider].AVAILABLE_MODELS]
        requested_model = arguments.get("model")
        target_model = requested_model if requested_model in available_models else available_models[0]

        sub_assistant = SimpleNamespace(instructions="You are a helpful assistant.")
        try:
            sub_result, sub_usage, sub_used_global_key = await send_chat_message(
                sub_assistant, prompt, ai_provider=target_provider, model=target_model, user=user,
                stream=False, allow_delegation=False,
            )
        except Exception as exc:
            logger.exception("Delegated call to %s/%s failed", target_provider, target_model)
            return f"Delegation to {target_provider}/{target_model} failed: {exc}"

        if sub_used_global_key:
            await deduct_credits(user, target_provider, target_model, sub_usage)

        return f"[Response from {target_provider}/{target_model}]\n\n{sub_result}"

    return DELEGATE_TOOL, executor


def _compute_cost_credits(provider_cls, model: str, usage: UsageAccumulator | None) -> int:
    if provider_cls is None or usage is None:
        return 0
    pricing = provider_cls.PRICING.get(model)
    if not pricing:
        return 0
    cost_usd = (
        usage.input_tokens / 1_000_000 * pricing["input"]
        + usage.output_tokens / 1_000_000 * pricing["output"]
    )
    if cost_usd <= 0:
        return 0
    return max(1, math.ceil(cost_usd / CREDIT_VALUE_USD))


def _apply_credit_deduction(user, cost: int) -> None:
    from django.contrib.auth import get_user_model
    from django.db.models import F

    get_user_model().objects.filter(pk=user.pk).update(credits_remaining=F('credits_remaining') - cost)


async def deduct_credits(user, ai_provider: str, model: str, usage: UsageAccumulator | None) -> None:
    cost = _compute_cost_credits(PROVIDERS.get(ai_provider), model, usage)
    if cost > 0:
        await sync_to_async(_apply_credit_deduction)(user, cost)


async def send_chat_message(
    assistant,
    message_text: str,
    *,
    ai_provider: str,
    model: str,
    user,
    conversation_history: list[dict] | None = None,
    memories: list[str] | None = None,
    stream: bool = False,
    project_id=None,
    on_tool_call=None,
    confirm_tool_call=None,
    allow_delegation: bool = True,
):
    from keys.services import get_user_api_key

    key_record = await get_user_api_key(user, ai_provider)
    api_key = key_record.encrypted_key if key_record else None
    used_global_key = key_record is None

    if used_global_key and user.credits_remaining <= 0:
        raise InsufficientCreditsError(
            "Crédit épuisé sur la clé partagée. Ajoute ta propre clé API dans Paramètres pour continuer."
        )

    try:
        provider = get_provider(ai_provider, api_key=api_key)
        system = _build_system_prompt(assistant.instructions, memories or [])
        messages = [*(conversation_history or []), {"role": "user", "content": message_text}]
        tools, mcp_executor = await _get_mcp_context(project_id)

        image_tool, image_executor = _build_image_tool(ai_provider, api_key, user, used_global_key)
        if image_tool is not None:
            tools = [*tools, image_tool]

        if allow_delegation:
            delegate_tool, delegate_executor = _build_delegate_tool(user, confirm_tool_call)
            tools = [*tools, delegate_tool]
        else:
            delegate_tool, delegate_executor = None, None

        async def combined_executor(name: str, arguments: dict) -> str:
            if image_tool is not None and name == "generate_image":
                return await image_executor(arguments)
            if delegate_tool is not None and name == "delegate_to_model":
                return await delegate_executor(arguments)
            if mcp_executor is not None:
                return await mcp_executor(name, arguments)
            raise ValueError(f"Unknown tool: {name}")

        tool_executor = combined_executor if tools else None

        turn = SimpleNamespace(model=model, instructions=assistant.instructions)
        usage = UsageAccumulator()
        if stream:
            result = provider.stream(turn, messages, system, tools, tool_executor, usage=usage, on_tool_call=on_tool_call)
        else:
            result = await run_agent_loop(
                provider, turn, messages, system, tools, tool_executor, usage=usage, on_tool_call=on_tool_call,
            )
        return result, usage, used_global_key
    except Exception:
        logger.exception("Error during chat dispatch")
        raise
