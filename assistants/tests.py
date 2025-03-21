from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from assistants.models import Assistant

User = get_user_model()

class AssistantCRUDAPITest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.client.login(username='testuser', password='testpass')
        self.assistant_data = {
            "name": "Test Assistant",
            "instructions": "Be helpful.",
            "model": "gpt-4o",
            "ai_provider": "openai"
        }
        self.url = reverse('assistant-list')


    def test_create_assistant(self):
        response = self.client.post(self.url, data=self.assistant_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("provider_assistant_id", response.data)
        self.assertIsNotNone(response.data["provider_assistant_id"])

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
