import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from image_providers.openai_image.provider import OpenAIImageProvider


def run(coro):
    return asyncio.run(coro)


class OpenAIImageProviderTest(SimpleTestCase):
    def setUp(self):
        with patch('image_providers.openai_image.provider.AsyncOpenAI'):
            self.provider = OpenAIImageProvider()

    def _make_response(self, b64_json=None, url=None, input_tokens=10, output_tokens=1290):
        image = MagicMock(b64_json=b64_json, url=url)
        usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
        return MagicMock(data=[image], usage=usage)

    def test_generate_decodes_b64_json(self):
        raw_bytes = b'fake-png-bytes'
        b64_json = base64.b64encode(raw_bytes).decode()
        self.provider.client.images.generate = AsyncMock(return_value=self._make_response(b64_json=b64_json))

        result = run(self.provider.generate('a cat'))

        self.assertEqual(result.data, raw_bytes)
        self.assertEqual(result.mime_type, 'image/png')

    def test_generate_captures_usage(self):
        b64_json = base64.b64encode(b'x').decode()
        self.provider.client.images.generate = AsyncMock(
            return_value=self._make_response(b64_json=b64_json, input_tokens=42, output_tokens=1290)
        )

        result = run(self.provider.generate('a cat'))

        self.assertEqual(result.usage, {'input_tokens': 42, 'output_tokens': 1290})

    def test_generate_uses_correct_model(self):
        b64_json = base64.b64encode(b'x').decode()
        self.provider.client.images.generate = AsyncMock(return_value=self._make_response(b64_json=b64_json))

        run(self.provider.generate('a cat'))

        call_kwargs = self.provider.client.images.generate.call_args.kwargs
        self.assertEqual(call_kwargs['model'], 'gpt-image-2')
        self.assertEqual(call_kwargs['prompt'], 'a cat')

    def test_generate_falls_back_to_url_when_no_b64(self):
        self.provider.client.images.generate = AsyncMock(
            return_value=self._make_response(b64_json=None, url='https://example.com/img.png')
        )

        fake_http_response = MagicMock(content=b'downloaded-bytes')
        fake_http_response.raise_for_status = MagicMock()

        with patch('image_providers.openai_image.provider.httpx.AsyncClient') as mock_client_cls:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=fake_http_response)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = run(self.provider.generate('a cat'))

        self.assertEqual(result.data, b'downloaded-bytes')
