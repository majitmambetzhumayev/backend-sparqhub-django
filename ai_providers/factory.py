from contextlib import asynccontextmanager

from .anthropic.anthropic_provider import AnthropicProvider
from .openai.openai_provider import OpenAIProvider
from .mistral.mistral_provider import MistralProvider
from .google.google_provider import GeminiProvider

PROVIDERS = {
    'anthropic': AnthropicProvider,
    'openai': OpenAIProvider,
    'mistral': MistralProvider,
    'gemini': GeminiProvider,
}


def get_provider(provider_name: str, api_key: str | None = None):
    cls = PROVIDERS.get(provider_name.lower())
    if cls is None:
        raise ValueError(f"Unsupported provider: {provider_name}")
    return cls(api_key=api_key)


@asynccontextmanager
async def provider_session(provider_name: str, api_key: str | None = None):
    """For call sites that make a single, self-contained use of a provider
    (as opposed to chat_router.py, which keeps one alive across a streamed
    response) — guarantees `aclose()` runs before the block exits, rather
    than relying on garbage collection to close it whenever, which is unsafe
    once the event loop that created it (e.g. a Celery task run via
    async_to_sync) has already been torn down."""
    provider = get_provider(provider_name, api_key=api_key)
    try:
        yield provider
    finally:
        await provider.aclose()
