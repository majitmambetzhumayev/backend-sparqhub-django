# image_providers/gemini_image/provider.py
import base64

from google import genai
from django.conf import settings

from image_providers.base import ImageProviderBase, ImageResult


class GeminiImageProvider(ImageProviderBase):
    label = "Gemini"
    MODEL = "gemini-2.5-flash-image"
    # USD per 1M tokens. Output is Google's published rate for this model
    # ($30/1M image output tokens); input approximated at the base
    # gemini-2.5-flash text rate since prompts here are short text only.
    PRICING = {
        "gemini-2.5-flash-image": {"input": 0.30, "output": 30.00},
    }

    def __init__(self, api_key: str | None = None):
        self.client = genai.Client(api_key=api_key or settings.GEMINI_API_KEY)

    async def generate(self, prompt: str) -> ImageResult:
        interaction = await self.client.aio.interactions.create(model=self.MODEL, input=prompt)
        image = interaction.output_image
        data = base64.b64decode(image.data)

        usage = interaction.usage
        usage_dict = {
            "input_tokens": usage.total_input_tokens if usage else 0,
            "output_tokens": usage.total_output_tokens if usage else 0,
        }
        return ImageResult(data=data, mime_type=image.mime_type or "image/png", usage=usage_dict)
