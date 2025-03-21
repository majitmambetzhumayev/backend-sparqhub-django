from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from assistants.models import Assistant
from threads.models import Thread

User = get_user_model()

class QuickChatDataAPITest(APITestCase):
    def setUp(self):
        # Create a test user and log them in
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')
        
        # Create a sample assistant and thread for the user
        self.assistant = Assistant.objects.create(
            user=self.user,
            provider_assistant_id='assistant-123',
            name='Test Assistant',
            instructions='Some instructions',
            model='gpt-4',
            metadata={}
        )
        
        self.thread = Thread.objects.create(
            user=self.user,
            assistant=self.assistant,
            conversation_state=[],
        )
        
        # Get URL for the aggregated data endpoint
        # Ensure that your URL configuration has named the endpoint 'quick-chat-data'
        self.url = reverse('quick-chat-data')

    def test_quick_chat_data_endpoint(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check that the response contains expected keys
        self.assertIn('assistants', response.data)
        self.assertIn('default_assistant', response.data)
        self.assertIn('default_thread', response.data)
        
        # Optionally, verify the returned data is correct
        assistants_data = response.data.get('assistants')
        self.assertTrue(len(assistants_data) > 0)
        self.assertEqual(response.data['default_assistant'], self.assistant.id)
        self.assertEqual(response.data['default_thread'], self.thread.id)
