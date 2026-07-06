import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from image_providers.gemini_image.provider import GeminiImageProvider


def run(coro):
    return asyncio.run(coro)


class GeminiImageProviderTest(SimpleTestCase):
    def setUp(self):
        with patch('image_providers.gemini_image.provider.genai.Client'):
            self.provider = GeminiImageProvider()

    def _make_interaction(self, data, mime_type='image/png', total_input_tokens=8, total_output_tokens=1290):
        output_image = MagicMock(data=data, mime_type=mime_type)
        usage = MagicMock(total_input_tokens=total_input_tokens, total_output_tokens=total_output_tokens)
        return MagicMock(output_image=output_image, usage=usage)

    def test_generate_decodes_image_data(self):
        raw_bytes = b'fake-png-bytes'
        data = base64.b64encode(raw_bytes).decode()
        self.provider.client.aio.interactions.create = AsyncMock(return_value=self._make_interaction(data))

        result = run(self.provider.generate('a cat'))

        self.assertEqual(result.data, raw_bytes)
        self.assertEqual(result.mime_type, 'image/png')

    def test_generate_captures_usage(self):
        data = base64.b64encode(b'x').decode()
        self.provider.client.aio.interactions.create = AsyncMock(
            return_value=self._make_interaction(data, total_input_tokens=5, total_output_tokens=1290)
        )

        result = run(self.provider.generate('a cat'))

        self.assertEqual(result.usage, {'input_tokens': 5, 'output_tokens': 1290})

    def test_generate_uses_correct_model(self):
        data = base64.b64encode(b'x').decode()
        self.provider.client.aio.interactions.create = AsyncMock(return_value=self._make_interaction(data))

        run(self.provider.generate('a cat'))

        call_kwargs = self.provider.client.aio.interactions.create.call_args.kwargs
        self.assertEqual(call_kwargs['model'], 'gemini-2.5-flash-image')
        self.assertEqual(call_kwargs['input'], 'a cat')
