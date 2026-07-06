# image_providers/factory.py
from .openai_image.provider import OpenAIImageProvider
from .gemini_image.provider import GeminiImageProvider

IMAGE_PROVIDERS = {
    'openai': OpenAIImageProvider,
    'gemini': GeminiImageProvider,
}


def get_image_provider(provider_name: str, api_key: str | None = None):
    """Returns None (not an error) when the given chat provider has no
    matching image-generation capability — callers use this to decide
    whether to offer the generate_image tool at all."""
    cls = IMAGE_PROVIDERS.get(provider_name.lower())
    if cls is None:
        return None
    return cls(api_key=api_key)
