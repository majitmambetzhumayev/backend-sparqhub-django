# ai_providers/factory.py

from .openai.interface import OpenAIAssistantInterface
# If you later implement these providers:
# from .mistral.mistral_provider import MistralProvider
# from .anthropic.anthropic_provider import AnthropicProvider
# from .google.google_provider import GoogleProvider

def get_provider(provider_name: str):
    provider_name = provider_name.lower()
    if provider_name == "openai":
        return OpenAIAssistantInterface()
    # elif provider_name == "mistral":
    #     return MistralProvider()
    # elif provider_name == "anthropic":
    #     return AnthropicProvider()
    # elif provider_name == "google":
    #     return GoogleProvider()
    else:
        raise ValueError(f"Unsupported provider: {provider_name}")
