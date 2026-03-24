from .anthropic.anthropic_provider import AnthropicProvider


def get_provider(provider_name: str):
    providers = {
        'anthropic': AnthropicProvider,
    }
    cls = providers.get(provider_name.lower())
    if cls is None:
        raise ValueError(f"Unsupported provider: {provider_name}")
    return cls()
