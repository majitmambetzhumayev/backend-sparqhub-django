from unittest.mock import patch

from django.test import SimpleTestCase

from image_providers.factory import IMAGE_PROVIDERS, get_image_provider
from image_providers.openai_image.provider import OpenAIImageProvider
from image_providers.gemini_image.provider import GeminiImageProvider


class ImageProviderFactoryTest(SimpleTestCase):
    def test_get_openai_image_provider(self):
        provider = get_image_provider('openai', api_key='test-key')
        self.assertIsInstance(provider, OpenAIImageProvider)

    def test_get_gemini_image_provider(self):
        provider = get_image_provider('gemini', api_key='test-key')
        self.assertIsInstance(provider, GeminiImageProvider)

    def test_returns_none_for_provider_without_image_support(self):
        self.assertIsNone(get_image_provider('anthropic'))
        self.assertIsNone(get_image_provider('mistral'))

    def test_returns_none_for_unknown_provider(self):
        self.assertIsNone(get_image_provider('unknown'))

    def test_all_registered_providers_are_resolvable(self):
        for name in IMAGE_PROVIDERS:
            self.assertIsInstance(get_image_provider(name, api_key='test-key'), IMAGE_PROVIDERS[name])

    def test_openai_uses_provided_api_key_over_settings_default(self):
        provider = get_image_provider('openai', api_key='sk-personal-key')
        self.assertEqual(provider.client.api_key, 'sk-personal-key')

    def test_gemini_uses_provided_api_key_over_settings_default(self):
        with patch('image_providers.gemini_image.provider.genai.Client') as mock_client:
            get_image_provider('gemini', api_key='sk-personal-key')
        mock_client.assert_called_once_with(api_key='sk-personal-key')
