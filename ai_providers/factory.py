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
