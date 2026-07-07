import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from ai_providers.base import ProviderResponse
from librarian.services import extract_and_store_memories
from librarian.tasks import extract_memories_task


def run(coro):
    return asyncio.run(coro)


class ExtractAndStoreMemoriesTest(SimpleTestCase):
    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    @patch('librarian.services.store_memory')
    @patch('ai_providers.factory.get_provider')
    def test_stores_one_entry_per_extracted_fact(self, mock_get_provider, mock_store_memory, mock_get_key):
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=ProviderResponse(
            text="Allergic to peanuts.\nWorks as a data scientist.", tool_calls=[],
        ))
        provider.aclose = AsyncMock()
        mock_get_provider.return_value = provider
        user = MagicMock()
        assistant = MagicMock(ai_provider='anthropic')

        run(extract_and_store_memories(
            user, assistant, "I'm allergic to peanuts and I'm a data scientist", "Noted!",
        ))

        self.assertEqual(mock_store_memory.call_count, 2)
        mock_store_memory.assert_any_call(user, "Allergic to peanuts.")
        mock_store_memory.assert_any_call(user, "Works as a data scientist.")

    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    @patch('librarian.services.store_memory')
    @patch('ai_providers.factory.get_provider')
    def test_none_response_stores_nothing(self, mock_get_provider, mock_store_memory, mock_get_key):
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=ProviderResponse(text="NONE", tool_calls=[]))
        provider.aclose = AsyncMock()
        mock_get_provider.return_value = provider

        run(extract_and_store_memories(MagicMock(), MagicMock(ai_provider='anthropic'), "hey", "hi there"))

        mock_store_memory.assert_not_called()


class ExtractMemoriesTaskTest(SimpleTestCase):
    @patch('librarian.tasks.extract_and_store_memories', new_callable=AsyncMock)
    @patch('librarian.tasks.Assistant')
    @patch('librarian.tasks.get_user_model')
    def test_resolves_user_and_assistant_then_extracts(self, mock_get_user_model, mock_assistant_cls, mock_extract):
        user = MagicMock()
        assistant = MagicMock()
        mock_get_user_model.return_value.objects.get.return_value = user
        mock_assistant_cls.objects.get.return_value = assistant

        extract_memories_task(1, 2, "hi", "hello")

        mock_extract.assert_called_once_with(user, assistant, "hi", "hello")

    @patch('librarian.tasks.logger')
    @patch('librarian.tasks.get_user_model')
    def test_logs_and_swallows_exception_on_lookup_failure(self, mock_get_user_model, mock_logger):
        mock_get_user_model.return_value.objects.get.side_effect = Exception("boom")

        extract_memories_task(1, 2, "hi", "hello")  # must not raise

        mock_logger.exception.assert_called_once()
