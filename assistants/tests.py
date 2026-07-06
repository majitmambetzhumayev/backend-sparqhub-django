from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from assistants.models import Assistant

User = get_user_model()

class AssistantCRUDAPITest(APITestCase):
    def setUp(self):
        # AssistantViewSet only authenticates via CookieJWTAuthentication, not
        # session auth, so client.login() (session-based) wouldn't satisfy it.
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.client.force_authenticate(user=self.user)
        self.assistant_data = {
            "name": "Test Assistant",
            "instructions": "Be helpful.",
            "model": "claude-sonnet-4-6",
            "ai_provider": "anthropic"
        }
        self.url = reverse('assistant-list')

    def test_create_assistant(self):
        # Assistant creation is purely local persistence — no remote provider call.
        response = self.client.post(self.url, data=self.assistant_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Test Assistant")

    def test_update_assistant(self):
        # Create an assistant first
        create_resp = self.client.post(self.url, data=self.assistant_data, format='json')
        assistant_id = create_resp.data["id"]
        detail_url = reverse('assistant-detail', kwargs={'pk': assistant_id})
        
        # Update the assistant's name
        update_data = {"name": "Updated Assistant"}
        update_resp = self.client.patch(detail_url, data=update_data, format='json')
        self.assertEqual(update_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(update_resp.data["name"], "Updated Assistant")

    def test_delete_assistant(self):
        # Create an assistant first
        create_resp = self.client.post(self.url, data=self.assistant_data, format='json')
        assistant_id = create_resp.data["id"]
        detail_url = reverse('assistant-detail', kwargs={'pk': assistant_id})

        # Delete the assistant
        delete_resp = self.client.delete(detail_url)
        self.assertEqual(delete_resp.status_code, status.HTTP_204_NO_CONTENT)
        # Retrieve from DB and check the deleted flag is set
        from assistants.models import Assistant
        assistant = Assistant.objects.get(id=assistant_id)
        self.assertTrue(assistant.deleted)

    def test_persistent_default_assistant_excluded_from_crud(self):
        # The implicit default assistant (used internally for chat) must never
        # show up in or be reachable via the manual Assistant Manager CRUD API —
        # it has no name/instructions the user picked and deleting it would
        # cascade-delete every conversation thread.
        from assistants.services import get_or_create_default_assistant

        default_assistant = get_or_create_default_assistant(self.user)

        list_resp = self.client.get(self.url)
        self.assertNotIn(default_assistant.id, [a["id"] for a in list_resp.data])

        detail_url = reverse('assistant-detail', kwargs={'pk': default_assistant.id})
        self.assertEqual(self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.delete(detail_url).status_code, status.HTTP_404_NOT_FOUND)


class AvailableProvidersAPITest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.client.login(username='testuser', password='testpass')

    def test_lists_all_registered_providers_with_models(self):
        response = self.client.get(reverse('providers'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for provider_name in ('anthropic', 'openai', 'mistral', 'gemini'):
            self.assertIn(provider_name, response.data)
            self.assertTrue(len(response.data[provider_name]['models']) > 0)
        self.assertNotIn('unknown', response.data)
