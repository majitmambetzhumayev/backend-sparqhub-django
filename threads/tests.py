# threads/tests.py
from django.test import TestCase
from django.contrib.auth import get_user_model
from threads.models import Thread
from assistants.models import Assistant

User = get_user_model()

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

    def test_conversation_state_update(self):
        thread = Thread.objects.create(user=self.user, assistant=self.assistant)
        # Simulate conversation state updates
        thread.conversation_state.append({"role": "user", "content": "Hello"})
        thread.conversation_state.append({"role": "assistant", "content": "Hi there!"})
        thread.save()
        thread.refresh_from_db()
        self.assertEqual(len(thread.conversation_state), 2)
