import logging

from ai_providers.factory import get_provider

logger = logging.getLogger(__name__)


def _build_system_prompt(base: str, memories: list[str]) -> str:
    if not memories:
        return base
    context = "\n".join(f"- {m}" for m in memories)
    return f"{base}\n\nRelevant context from memory:\n{context}"


def send_chat_message(assistant, message_text: str, memories: list[str] | None = None, stream: bool = False):
    try:
        provider = get_provider(assistant.ai_provider)
        system = _build_system_prompt(assistant.instructions, memories or [])
        messages = [{"role": "user", "content": message_text}]
        return provider.chat(assistant, messages, system=system, stream=stream)
    except Exception:
        logger.exception("Error during chat dispatch")
        raise
