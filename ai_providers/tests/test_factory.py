# ai_providers/tests/test_factory.py

from django.test import TestCase
from ai_providers.factory import get_provider
from ai_providers.openai.interface import OpenAIAssistantInterface

class ProviderFactoryTest(TestCase):
    def test_get_openai_provider(self):
        provider = get_provider("openai")
        self.assertIsInstance(provider, OpenAIAssistantInterface)
