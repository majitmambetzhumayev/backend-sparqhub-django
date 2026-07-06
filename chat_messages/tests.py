# chat_messages/tests.py
import asyncio
import time
from unittest.mock import AsyncMock, patch

from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status

from ai_providers.chat_router import InsufficientCreditsError
from assistants.models import Assistant
from chat_messages.consumers import ConversationConsumer
from chat_messages.models import Message
from chat_messages.services import send_message
from threads.models import Thread

User = get_user_model()


def run(coro):
    return asyncio.run(coro)


class SendMessageServiceTest(TransactionTestCase):
    # send_message() hops onto a different thread/DB connection via
    # sync_to_async; a plain TestCase's uncommitted transaction isn't visible
    # there, causing spurious FK violations against rows created in setUp().
    def setUp(self):
        self.user = User.objects.create_user(username="chatuser", password="pass")
        self.assistant = Assistant.objects.create(
            user=self.user, name="Chat Assistant", instructions="Be concise.",
        )
        self.thread = Thread.objects.create(user=self.user, assistant=self.assistant, conversation_state=[])

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.services.send_chat_message", new_callable=AsyncMock)
    def test_records_messages_and_updates_conversation_state(self, mock_send, mock_extract_task, mock_title_task):
        mock_send.return_value = ("Hi there!", None, False)

        result = run(send_message(self.thread, "Hello", self.user))

        self.assertEqual(result, "Hi there!")
        self.thread.refresh_from_db()
        self.assertEqual(
            self.thread.conversation_state,
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ],
        )
        self.assertEqual(Message.objects.filter(thread=self.thread).count(), 2)
        mock_extract_task.delay.assert_called_once_with(
            self.thread.user_id, self.thread.assistant_id, "Hello", "Hi there!",
        )

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.services.send_chat_message", new_callable=AsyncMock)
    def test_records_turn_bumps_updated_at(self, mock_send, mock_extract_task, mock_title_task):
        # Regression test: update_fields previously omitted "updated_at", so
        # Django's auto_now never actually persisted the new timestamp — the
        # conversation list's "most recently used first" ordering silently
        # never worked past thread creation.
        mock_send.return_value = ("Hi there!", None, False)
        before = self.thread.updated_at
        time.sleep(1.1)

        run(send_message(self.thread, "Hello", self.user))

        self.thread.refresh_from_db()
        self.assertGreater(self.thread.updated_at, before)

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.services.send_chat_message", new_callable=AsyncMock)
    def test_first_turn_sets_fallback_title_and_fires_title_task(self, mock_send, mock_extract_task, mock_title_task):
        mock_send.return_value = ("Hi there!", None, False)

        run(send_message(self.thread, "Hello", self.user))

        self.thread.refresh_from_db()
        self.assertEqual(self.thread.title, "Hello")
        mock_title_task.delay.assert_called_once_with(self.thread.id, "Hello", "Hi there!")

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.services.send_chat_message", new_callable=AsyncMock)
    def test_second_call_passes_prior_history(self, mock_send, mock_extract_task, mock_title_task):
        mock_send.return_value = ("First reply", None, False)
        run(send_message(self.thread, "First message", self.user))

        mock_send.return_value = ("Second reply", None, False)
        run(send_message(self.thread, "Second message", self.user))

        _, kwargs = mock_send.call_args
        self.assertEqual(kwargs["ai_provider"], self.thread.ai_provider)
        self.assertEqual(kwargs["model"], self.thread.model)
        self.assertEqual(
            kwargs["conversation_history"],
            [
                {"role": "user", "content": "First message"},
                {"role": "assistant", "content": "First reply"},
            ],
        )
        self.thread.refresh_from_db()
        self.assertEqual(len(self.thread.conversation_state), 4)
        # title generation only fires once, on the first turn
        mock_title_task.delay.assert_called_once()

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.deduct_credits", new_callable=AsyncMock)
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.services.send_chat_message", new_callable=AsyncMock)
    def test_deducts_credits_when_global_key_used(self, mock_send, mock_extract_task, mock_deduct, mock_title_task):
        mock_send.return_value = ("Hi there!", "usage-marker", True)

        run(send_message(self.thread, "Hello", self.user))

        mock_deduct.assert_awaited_once_with(self.user, self.thread.ai_provider, self.thread.model, "usage-marker")

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.deduct_credits", new_callable=AsyncMock)
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.services.send_chat_message", new_callable=AsyncMock)
    def test_does_not_deduct_credits_when_personal_key_used(self, mock_send, mock_extract_task, mock_deduct, mock_title_task):
        mock_send.return_value = ("Hi there!", "usage-marker", False)

        run(send_message(self.thread, "Hello", self.user))

        mock_deduct.assert_not_awaited()


class ConversationConsumerTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="wsuser", password="pass")

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.consumers.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.services.send_chat_message")
    def test_streams_chunks_then_done_and_saves_message(self, mock_send, mock_memories, mock_extract_task, mock_title_task):
        # Mock one level below stream_message (not stream_message itself) so the
        # real _record_turn/persistence logic actually runs and can be asserted on.
        async def fake_chunks():
            for chunk in ["Hel", "lo!"]:
                yield chunk

        async def fake_send_chat_message(*args, **kwargs):
            return fake_chunks(), None, False

        mock_send.side_effect = fake_send_chat_message

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"thread_id": None, "message": "Hi"})
            frames = [await communicator.receive_json_from() for _ in range(4)]
            await communicator.disconnect()
            return frames

        frames = run(scenario())
        self.assertEqual(frames[0], {"status": "thinking"})
        self.assertEqual(frames[1], {"chunk": "Hel"})
        self.assertEqual(frames[2], {"chunk": "lo!"})
        self.assertEqual(frames[3]["done"], True)

        thread = Thread.objects.get(pk=frames[3]["thread_id"])
        self.assertEqual(thread.conversation_state[-1], {"role": "assistant", "content": "Hello!"})
        self.assertTrue(thread.assistant.is_persistent)

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.consumers.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.services.send_chat_message")
    def test_sends_tool_call_status_frame(self, mock_send, mock_memories, mock_extract_task, mock_title_task):
        async def fake_send_chat_message(*args, **kwargs):
            await kwargs["on_tool_call"]("search_memories")

            async def fake_chunks():
                yield "Done."

            return fake_chunks(), None, False

        mock_send.side_effect = fake_send_chat_message

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"thread_id": None, "message": "Hi"})
            frames = [await communicator.receive_json_from() for _ in range(4)]
            await communicator.disconnect()
            return frames

        frames = run(scenario())
        self.assertEqual(frames[0], {"status": "thinking"})
        self.assertEqual(frames[1], {"status": "tool_call", "tool": "search_memories"})
        self.assertEqual(frames[2], {"chunk": "Done."})
        self.assertEqual(frames[3]["done"], True)

    def test_rejects_anonymous_connection(self):
        from django.contrib.auth.models import AnonymousUser

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = AnonymousUser()
            return await communicator.connect()

        connected, _ = run(scenario())
        self.assertFalse(connected)

    @patch("chat_messages.consumers.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.services.send_chat_message", new_callable=AsyncMock)
    def test_sends_error_frame_on_insufficient_credits(self, mock_send, mock_memories):
        mock_send.side_effect = InsufficientCreditsError("Crédit épuisé.")

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"thread_id": None, "message": "Hi"})
            thinking_frame = await communicator.receive_json_from()
            error_frame = await communicator.receive_json_from()
            await communicator.disconnect()
            return thinking_frame, error_frame

        thinking_frame, error_frame = run(scenario())
        self.assertEqual(thinking_frame, {"status": "thinking"})
        self.assertEqual(error_frame, {"error": "Crédit épuisé."})

    @patch("chat_messages.consumers.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.services.send_chat_message", new_callable=AsyncMock)
    def test_sends_generic_error_frame_on_unexpected_exception(self, mock_send, mock_memories):
        # A tool/provider failure (rate limit, network error, etc.) should surface
        # as a clean error frame, not crash the WebSocket connection outright.
        mock_send.side_effect = RuntimeError("boom")

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"thread_id": None, "message": "Hi"})
            thinking_frame = await communicator.receive_json_from()
            error_frame = await communicator.receive_json_from()
            await communicator.disconnect()
            return thinking_frame, error_frame

        thinking_frame, error_frame = run(scenario())
        self.assertEqual(thinking_frame, {"status": "thinking"})
        self.assertEqual(error_frame, {"error": "Something went wrong while generating the response. Please try again."})

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.consumers.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.services.send_chat_message")
    def test_pauses_for_confirmation_and_resumes_when_confirmed(
        self, mock_send, mock_memories, mock_extract_task, mock_title_task,
    ):
        async def fake_send_chat_message(*args, **kwargs):
            confirmed = await kwargs["confirm_tool_call"]("delegate_to_model", {"provider": "gemini"})

            async def fake_chunks():
                yield "Confirmed!" if confirmed else "Declined."

            return fake_chunks(), None, False

        mock_send.side_effect = fake_send_chat_message

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"thread_id": None, "message": "Hi"})
            thinking_frame = await communicator.receive_json_from()
            confirm_frame = await communicator.receive_json_from()

            await communicator.send_json_to({"type": "tool_confirmation", "confirmed": True})
            chunk_frame = await communicator.receive_json_from()
            done_frame = await communicator.receive_json_from()

            await communicator.disconnect()
            return thinking_frame, confirm_frame, chunk_frame, done_frame

        thinking_frame, confirm_frame, chunk_frame, done_frame = run(scenario())
        self.assertEqual(thinking_frame, {"status": "thinking"})
        self.assertEqual(confirm_frame, {"status": "confirm_required", "tool": "delegate_to_model", "arguments": {"provider": "gemini"}})
        self.assertEqual(chunk_frame, {"chunk": "Confirmed!"})
        self.assertTrue(done_frame["done"])

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.consumers.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.services.send_chat_message")
    def test_resumes_with_declined_result_when_not_confirmed(
        self, mock_send, mock_memories, mock_extract_task, mock_title_task,
    ):
        async def fake_send_chat_message(*args, **kwargs):
            confirmed = await kwargs["confirm_tool_call"]("delegate_to_model", {"provider": "gemini"})

            async def fake_chunks():
                yield "Confirmed!" if confirmed else "Declined."

            return fake_chunks(), None, False

        mock_send.side_effect = fake_send_chat_message

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"thread_id": None, "message": "Hi"})
            await communicator.receive_json_from()  # thinking
            await communicator.receive_json_from()  # confirm_required

            await communicator.send_json_to({"type": "tool_confirmation", "confirmed": False})
            chunk_frame = await communicator.receive_json_from()

            await communicator.disconnect()
            return chunk_frame

        chunk_frame = run(scenario())
        self.assertEqual(chunk_frame, {"chunk": "Declined."})


class SendMessageAPICreditsTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="apiuser", password="pass")
        self.client.force_authenticate(user=self.user)

    @patch("chat_messages.views.send_message", new_callable=AsyncMock)
    def test_returns_402_on_insufficient_credits(self, mock_send):
        mock_send.side_effect = InsufficientCreditsError("Crédit épuisé.")

        response = self.client.post(reverse('message-list-create-thread'), {"message": "Hi"}, format='json')

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(response.data["error"], "Crédit épuisé.")
