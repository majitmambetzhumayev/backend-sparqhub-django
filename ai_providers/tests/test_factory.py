# ai_providers/tests/test_factory.py
import asyncio
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from ai_providers.anthropic.anthropic_provider import AnthropicProvider
from ai_providers.openai.openai_provider import OpenAIProvider
from ai_providers.mistral.mistral_provider import MistralProvider
from ai_providers.google.google_provider import GeminiProvider
from ai_providers.factory import PROVIDERS, get_provider, provider_session


def run(coro):
    return asyncio.run(coro)


class ProviderFactoryTest(SimpleTestCase):
    def test_get_anthropic_provider(self):
        provider = get_provider("anthropic")
        self.assertIsInstance(provider, AnthropicProvider)

    def test_get_openai_provider(self):
        provider = get_provider("openai", api_key="test-key")
        self.assertIsInstance(provider, OpenAIProvider)

    def test_get_mistral_provider(self):
        provider = get_provider("mistral", api_key="test-key")
        self.assertIsInstance(provider, MistralProvider)

    def test_get_gemini_provider(self):
        provider = get_provider("gemini", api_key="test-key")
        self.assertIsInstance(provider, GeminiProvider)

    def test_all_registered_providers_are_resolvable(self):
        # Explicit api_key so this doesn't depend on real provider keys being
        # configured in the environment — some SDKs (e.g. OpenAI's) validate
        # credentials eagerly at client construction.
        for name in PROVIDERS:
            self.assertIsInstance(get_provider(name, api_key="test-key"), PROVIDERS[name])

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            get_provider("unknown")

    def test_uses_provided_api_key_over_settings_default(self):
        provider = get_provider("anthropic", api_key="sk-personal-key")
        self.assertEqual(provider.client.api_key, "sk-personal-key")

    def test_falls_back_to_settings_default_when_no_api_key(self):
        from django.conf import settings

        provider = get_provider("anthropic")
        self.assertEqual(provider.client.api_key, settings.ANTHROPIC_API_KEY)

    def test_openai_uses_provided_api_key_over_settings_default(self):
        provider = get_provider("openai", api_key="sk-personal-key")
        self.assertEqual(provider.client.api_key, "sk-personal-key")

    def test_mistral_uses_provided_api_key_over_settings_default(self):
        with patch("ai_providers.mistral.mistral_provider.Mistral") as mock_client:
            get_provider("mistral", api_key="sk-personal-key")
        mock_client.assert_called_once_with(api_key="sk-personal-key")

    def test_gemini_uses_provided_api_key_over_settings_default(self):
        with patch("ai_providers.google.google_provider.genai.Client") as mock_client:
            get_provider("gemini", api_key="sk-personal-key")
        mock_client.assert_called_once_with(api_key="sk-personal-key")


class ProviderSessionTest(SimpleTestCase):
    # Regression tests for the async_to_sync + Gemini "Event loop is closed"
    # bug: a provider created inside a short-lived event loop (a Celery task
    # via async_to_sync) must be explicitly closed before that loop tears
    # down, not left for garbage collection to close later on a dead loop.

    def test_closes_provider_after_successful_use(self):
        async def scenario():
            with patch("ai_providers.factory.get_provider") as mock_get_provider:
                provider = AsyncMock()
                mock_get_provider.return_value = provider
                async with provider_session("gemini", api_key="k") as p:
                    self.assertIs(p, provider)
                provider.aclose.assert_awaited_once()

        run(scenario())

    def test_closes_provider_even_if_the_block_raises(self):
        async def scenario():
            with patch("ai_providers.factory.get_provider") as mock_get_provider:
                provider = AsyncMock()
                mock_get_provider.return_value = provider
                with self.assertRaises(ValueError):
                    async with provider_session("gemini", api_key="k"):
                        raise ValueError("boom")
                provider.aclose.assert_awaited_once()

        run(scenario())
