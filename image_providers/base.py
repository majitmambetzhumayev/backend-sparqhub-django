# image_providers/base.py
from dataclasses import dataclass


@dataclass
class ImageResult:
    data: bytes
    mime_type: str
    usage: dict  # {"input_tokens": int, "output_tokens": int}


class ImageProviderBase:
    """
    Adapter contract for a single image-generation call. Distinct from
    AIProviderBase (ai_providers/base.py) since generating an image isn't a
    conversational turn — it has no append_turn/streaming concept.
    """

    label: str = ""
    MODEL: str = ""
    PRICING: dict = {}

    async def generate(self, prompt: str) -> ImageResult:
        raise NotImplementedError
