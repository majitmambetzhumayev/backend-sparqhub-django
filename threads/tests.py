# threads/tests.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import TestCase, SimpleTestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase, APITransactionTestCase
from rest_framework import status

from ai_providers.base import ProviderResponse
from threads.models import Thread
from threads.services import generate_and_store_title, get_or_create_thread
from threads.tasks import generate_thread_title_task
from assistants.models import Assistant
from projects.models import Project

User = get_user_model()


def run(coro):
    return asyncio.run(coro)

class ThreadModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='threaduser', password='pass')
        self.assistant = Assistant.objects.create(
            user=self.user,
            name="Test Assistant",
            instructions="Be concise.",
            model="gpt-4o",
            ai_provider="openai"
        )

    def test_create_thread(self):
        thread = Thread.objects.create(user=self.user, assistant=self.assistant)
        self.assertIsNotNone(thread.id)
        self.assertEqual(thread.user, self.user)
        self.assertEqual(thread.assistant, self.assistant)

    def test_ai_provider_and_model_default(self):
        thread = Thread.objects.create(user=self.user, assistant=self.assistant)
        self.assertEqual(thread.ai_provider, "anthropic")
        self.assertEqual(thread.model, "claude-sonnet-5")

    def test_conversation_state_update(self):
        thread = Thread.objects.create(user=self.user, assistant=self.assistant)
        # Simulate conversation state updates
        thread.conversation_state.append({"role": "user", "content": "Hello"})
        thread.conversation_state.append({"role": "assistant", "content": "Hi there!"})
        thread.save()
        thread.refresh_from_db()
        self.assertEqual(len(thread.conversation_state), 2)


class ThreadListAPITest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='listuser', password='pass')
        self.client.force_authenticate(user=self.user)
        self.assistant = Assistant.objects.create(user=self.user, name="A")

    def test_list_includes_title(self):
        thread = Thread.objects.create(
            user=self.user, assistant=self.assistant, title="Weather today",
        )
        response = self.client.get(reverse('thread-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = next(t for t in response.data if t["id"] == thread.id)
        self.assertEqual(item["title"], "Weather today")
        self.assertEqual(item["ai_provider"], "anthropic")

    def test_only_returns_own_threads(self):
        other_user = User.objects.create_user(username='otheruser', password='pass')
        other_assistant = Assistant.objects.create(user=other_user, name="B")
        Thread.objects.create(user=other_user, assistant=other_assistant)

        response = self.client.get(reverse('thread-list'))
        self.assertEqual(response.data, [])

    def test_filters_by_project_id(self):
        project = Project.objects.create(user=self.user, name="P")
        in_project = Thread.objects.create(user=self.user, assistant=self.assistant, project=project)
        Thread.objects.create(user=self.user, assistant=self.assistant)  # unassigned

        response = self.client.get(reverse('thread-list'), {"project_id": project.id})
        self.assertEqual([t["id"] for t in response.data], [in_project.id])

    def test_filters_by_project_id_none(self):
        project = Project.objects.create(user=self.user, name="P")
        Thread.objects.create(user=self.user, assistant=self.assistant, project=project)
        unassigned = Thread.objects.create(user=self.user, assistant=self.assistant)

        response = self.client.get(reverse('thread-list'), {"project_id": "none"})
        self.assertEqual([t["id"] for t in response.data], [unassigned.id])

    def test_no_project_id_returns_all(self):
        project = Project.objects.create(user=self.user, name="P")
        Thread.objects.create(user=self.user, assistant=self.assistant, project=project)
        Thread.objects.create(user=self.user, assistant=self.assistant)

        response = self.client.get(reverse('thread-list'))
        self.assertEqual(len(response.data), 2)


class ThreadDetailAPITest(APITransactionTestCase):
    # ThreadDetailAPIView.update() calls threads.services.update_thread_provider,
    # exercised synchronously here so a plain TestCase would be fine, but kept
    # consistent with the rest of this app's DB-backed test style.
    def setUp(self):
        self.user = User.objects.create_user(username='detailuser', password='pass')
        self.client.force_authenticate(user=self.user)
        self.assistant = Assistant.objects.create(user=self.user, name="A")
        self.thread = Thread.objects.create(user=self.user, assistant=self.assistant)

    def test_retrieve_excludes_conversation_state(self):
        response = self.client.get(reverse('thread-detail', kwargs={'pk': self.thread.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('conversation_state', response.data)

    def test_patch_updates_provider_and_model(self):
        response = self.client.patch(
            reverse('thread-detail', kwargs={'pk': self.thread.id}),
            data={"ai_provider": "anthropic", "model": "claude-opus-4-8"},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.model, "claude-opus-4-8")

    def test_patch_rejects_unsupported_model(self):
        response = self.client.patch(
            reverse('thread-detail', kwargs={'pk': self.thread.id}),
            data={"ai_provider": "anthropic", "model": "not-a-real-model"},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_updates_title_only(self):
        response = self.client.patch(
            reverse('thread-detail', kwargs={'pk': self.thread.id}),
            data={"title": "My renamed conversation"},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.title, "My renamed conversation")
        # untouched
        self.assertEqual(self.thread.ai_provider, "anthropic")
        self.assertEqual(self.thread.model, "claude-sonnet-5")

    def test_patch_provider_and_model_does_not_touch_title(self):
        self.thread.title = "Original title"
        self.thread.save(update_fields=["title"])

        response = self.client.patch(
            reverse('thread-detail', kwargs={'pk': self.thread.id}),
            data={"ai_provider": "anthropic", "model": "claude-opus-4-8"},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.title, "Original title")

    def test_delete_removes_thread(self):
        response = self.client.delete(reverse('thread-detail', kwargs={'pk': self.thread.id}))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Thread.objects.filter(pk=self.thread.id).exists())

    def test_cannot_access_another_users_thread(self):
        other_user = User.objects.create_user(username='otheruser2', password='pass')
        self.client.force_authenticate(user=other_user)
        response = self.client.get(reverse('thread-detail', kwargs={'pk': self.thread.id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_patch_reassigns_project(self):
        project = Project.objects.create(user=self.user, name="Target project")

        response = self.client.patch(
            reverse('thread-detail', kwargs={'pk': self.thread.id}),
            data={"project": project.id},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.project_id, project.id)

    def test_patch_unsets_project(self):
        project = Project.objects.create(user=self.user, name="P")
        self.thread.project = project
        self.thread.save(update_fields=["project"])

        response = self.client.patch(
            reverse('thread-detail', kwargs={'pk': self.thread.id}),
            data={"project": None},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.thread.refresh_from_db()
        self.assertIsNone(self.thread.project_id)

    def test_patch_rejects_another_users_project(self):
        other_user = User.objects.create_user(username='otherowner', password='pass')
        other_project = Project.objects.create(user=other_user, name="Not yours")

        response = self.client.patch(
            reverse('thread-detail', kwargs={'pk': self.thread.id}),
            data={"project": other_project.id},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_deleting_project_detaches_thread_instead_of_deleting_it(self):
        project = Project.objects.create(user=self.user, name="P")
        self.thread.project = project
        self.thread.save(update_fields=["project"])

        project.delete()

        self.thread.refresh_from_db()
        self.assertIsNone(self.thread.project_id)


class GetOrCreateThreadProjectTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='creatoruser', password='pass')

    def test_attaches_owned_project_on_creation(self):
        project = Project.objects.create(user=self.user, name="P")

        thread = get_or_create_thread(self.user, project_id=project.id)

        self.assertEqual(thread.project_id, project.id)

    def test_silently_ignores_unowned_project_id(self):
        other_user = User.objects.create_user(username='otherowner2', password='pass')
        other_project = Project.objects.create(user=other_user, name="Not yours")

        thread = get_or_create_thread(self.user, project_id=other_project.id)

        self.assertIsNone(thread.project_id)

    def test_no_project_id_leaves_thread_unassigned(self):
        thread = get_or_create_thread(self.user)

        self.assertIsNone(thread.project_id)


class GenerateAndStoreTitleTest(APITransactionTestCase):
    # generate_and_store_title() does an async DB write via sync_to_async; a
    # plain TestCase's uncommitted transaction from setUp() isn't visible
    # there (same gotcha documented elsewhere in this project's tests).
    def setUp(self):
        self.user = User.objects.create_user(username='titleuser', password='pass')
        self.assistant = Assistant.objects.create(user=self.user, name="A")
        self.thread = Thread.objects.create(user=self.user, assistant=self.assistant)

    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    @patch('ai_providers.factory.get_provider')
    def test_stores_generated_title(self, mock_get_provider, mock_get_key):
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=ProviderResponse(text='Weather Chat', tool_calls=[]))
        provider.aclose = AsyncMock()
        mock_get_provider.return_value = provider

        run(generate_and_store_title(self.thread, "What's the weather?", "Sunny today."))

        self.thread.refresh_from_db()
        self.assertEqual(self.thread.title, 'Weather Chat')

    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    @patch('ai_providers.factory.get_provider')
    def test_strips_quotes_from_generated_title(self, mock_get_provider, mock_get_key):
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=ProviderResponse(text='"Weather Chat"', tool_calls=[]))
        provider.aclose = AsyncMock()
        mock_get_provider.return_value = provider

        run(generate_and_store_title(self.thread, "hi", "hello"))

        self.thread.refresh_from_db()
        self.assertEqual(self.thread.title, 'Weather Chat')

    @patch('keys.services.get_user_api_key', new_callable=AsyncMock, return_value=None)
    @patch('ai_providers.factory.get_provider')
    def test_blank_response_does_not_overwrite_title(self, mock_get_provider, mock_get_key):
        self.thread.title = "Kept title"
        self.thread.save(update_fields=["title"])
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=ProviderResponse(text='   ', tool_calls=[]))
        provider.aclose = AsyncMock()
        mock_get_provider.return_value = provider

        run(generate_and_store_title(self.thread, "hi", "hello"))

        self.thread.refresh_from_db()
        self.assertEqual(self.thread.title, 'Kept title')


class GenerateThreadTitleTaskTest(SimpleTestCase):
    @patch('threads.tasks.generate_and_store_title', new_callable=AsyncMock)
    @patch('threads.tasks.Thread')
    def test_resolves_thread_then_generates_title(self, mock_thread_cls, mock_generate):
        thread = MagicMock()
        mock_thread_cls.objects.select_related.return_value.get.return_value = thread

        generate_thread_title_task(1, "hi", "hello")

        mock_generate.assert_called_once_with(thread, "hi", "hello")

    @patch('threads.tasks.logger')
    @patch('threads.tasks.Thread')
    def test_logs_and_swallows_exception_on_lookup_failure(self, mock_thread_cls, mock_logger):
        mock_thread_cls.objects.select_related.return_value.get.side_effect = Exception("boom")

        generate_thread_title_task(1, "hi", "hello")  # must not raise

        mock_logger.exception.assert_called_once()
