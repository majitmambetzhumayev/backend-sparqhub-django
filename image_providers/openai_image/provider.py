# image_providers/openai_image/provider.py
import base64

import httpx
from openai import AsyncOpenAI
from django.conf import settings

from image_providers.base import ImageProviderBase, ImageResult


class OpenAIImageProvider(ImageProviderBase):
    label = "OpenAI"
    MODEL = "gpt-image-2"
    # USD per 1M tokens (image generation is billed like a chat turn: a text
    # input token rate and an image output token rate).
    PRICING = {
        "gpt-image-2": {"input": 8.00, "output": 30.00},
    }

    def __init__(self, api_key: str | None = None):
        self.client = AsyncOpenAI(api_key=api_key or settings.OPENAI_API_KEY)

    async def generate(self, prompt: str) -> ImageResult:
        response = await self.client.images.generate(model=self.MODEL, prompt=prompt, n=1)
        image = response.data[0]

        if image.b64_json:
            data = base64.b64decode(image.b64_json)
        else:
            async with httpx.AsyncClient() as http_client:
                resp = await http_client.get(image.url)
                resp.raise_for_status()
                data = resp.content

        usage = response.usage
        usage_dict = {
            "input_tokens": usage.input_tokens if usage else 0,
            "output_tokens": usage.output_tokens if usage else 0,
        }
        return ImageResult(data=data, mime_type="image/png", usage=usage_dict)
