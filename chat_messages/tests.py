# chat_messages/tests.py
from django.test import TestCase
from agents import Agent, Runner
from ai_providers.openai.chat_api import chat_with_agent
from assistants.models import Assistant
from django.contrib.auth import get_user_model

User = get_user_model()

class ChatAPITest(TestCase):
    def setUp(self):
        # Create a dummy assistant record in the DB.
        self.user = User.objects.create_user(username='chatuser', password='pass')
        self.assistant = Assistant.objects.create(
            user=self.user,
            name="Chat Assistant",
            instructions="Provide concise answers.",
            model="gpt-4o",
            ai_provider="openai",
            provider_assistant_id="dummy-agent-id"
        )

    def test_chat_response_non_streaming(self):
        # Call your chat service method (assuming it wraps chat_with_agent)
        response = chat_with_agent(self.assistant, "Tell me a fun fact", stream=False)
        # Assuming it returns a list with one final result.
        self.assertIsInstance(response, list)
        self.assertGreater(len(response), 0)
