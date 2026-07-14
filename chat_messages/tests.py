# chat_messages/tests.py
import asyncio
import time
from unittest.mock import AsyncMock, patch

from channels.consumer import AsyncConsumer
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status

from ai_providers.chat_router import InsufficientCreditsError
from assistants.models import Assistant
from chat_messages import generation_registry
from chat_messages.consumers import ConversationConsumer
from chat_messages.models import Message
from chat_messages.services import send_message, _deduct_credits_after_persisted_turn, _record_turn
from projects.models import Project
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
    @patch("chat_messages.services.send_chat_message")
    def test_records_tool_calls_used_during_the_turn(self, mock_send, mock_extract_task, mock_title_task):
        async def fake_send_chat_message(*args, **kwargs):
            await kwargs["on_tool_call"]("search_memories")
            await kwargs["on_tool_call"]("generate_image")
            return "Hi there!", None, False

        mock_send.side_effect = fake_send_chat_message

        run(send_message(self.thread, "Hello", self.user))

        assistant_message = Message.objects.get(thread=self.thread, sender="assistant")
        self.assertEqual(assistant_message.tool_calls, ["search_memories", "generate_image"])
        user_message = Message.objects.get(thread=self.thread, sender="user")
        self.assertEqual(user_message.tool_calls, [])

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
    def test_returns_response_even_when_credit_deduction_fails(
        self, mock_send, mock_extract_task, mock_deduct, mock_title_task,
    ):
        # Regression test: by the time deduct_credits runs, the assistant's
        # reply is already saved and the caller has their answer — a
        # billing failure here must not turn a successful turn into a bare
        # 500, which would also invite a retry that pays for a second real
        # provider call while this one goes uncharged either way.
        mock_send.return_value = ("Hi there!", "usage-marker", True)
        mock_deduct.side_effect = RuntimeError("db blip")

        response_text = run(send_message(self.thread, "Hello", self.user))

        self.assertEqual(response_text, "Hi there!")
        self.assertEqual(Message.objects.filter(thread=self.thread, sender="assistant").count(), 1)

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.deduct_credits", new_callable=AsyncMock)
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.services.send_chat_message", new_callable=AsyncMock)
    def test_does_not_deduct_credits_when_personal_key_used(self, mock_send, mock_extract_task, mock_deduct, mock_title_task):
        mock_send.return_value = ("Hi there!", "usage-marker", False)

        run(send_message(self.thread, "Hello", self.user))

        mock_deduct.assert_not_awaited()


    def test_record_turn_rebuilds_conversation_state_from_message_table_not_stale_history(self):
        # Regression test for the conversation_state race: two concurrent
        # turns on the same thread (e.g. two open tabs) each capture `history`
        # before their AI call. If one turn's Message rows are persisted
        # before the other's _record_turn runs, appending onto the stale
        # `history` snapshot would silently drop that concurrent exchange.
        # _record_turn must rebuild from the Message table (source of truth).
        Message.objects.create(thread=self.thread, sender="user", content="Concurrent turn's message")
        Message.objects.create(thread=self.thread, sender="assistant", content="Concurrent turn's reply")

        stale_history = []  # captured before the concurrent turn committed anything
        _record_turn(self.thread, stale_history, "Hello", "Hi there!")

        self.thread.refresh_from_db()
        self.assertEqual(
            self.thread.conversation_state,
            [
                {"role": "user", "content": "Concurrent turn's message"},
                {"role": "assistant", "content": "Concurrent turn's reply"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ],
        )


class DeductCreditsAfterPersistedTurnTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="deductuser", password="pass")
        self.assistant = Assistant.objects.create(
            user=self.user, name="Chat Assistant", instructions="Be concise.",
        )
        self.thread = Thread.objects.create(user=self.user, assistant=self.assistant, conversation_state=[])

    @patch("chat_messages.services.deduct_credits", new_callable=AsyncMock)
    def test_swallows_and_logs_deduction_failure_instead_of_propagating(self, mock_deduct):
        # Regression test: this call sits after the turn's reply is already
        # saved — a billing failure here must never propagate and be
        # mistaken for "the whole turn failed."
        mock_deduct.side_effect = RuntimeError("db blip")

        run(_deduct_credits_after_persisted_turn(self.user, self.thread, "usage-marker"))  # must not raise

        mock_deduct.assert_awaited_once_with(self.user, self.thread.ai_provider, self.thread.model, "usage-marker")

    @patch("chat_messages.services.deduct_credits", new_callable=AsyncMock)
    def test_calls_through_on_success(self, mock_deduct):
        run(_deduct_credits_after_persisted_turn(self.user, self.thread, "usage-marker"))

        mock_deduct.assert_awaited_once_with(self.user, self.thread.ai_provider, self.thread.model, "usage-marker")


class ConversationConsumerTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="wsuser", password="pass")
        self.assistant = Assistant.objects.create(
            user=self.user, name="Chat Assistant", instructions="Be concise.",
        )
        self.existing_thread = Thread.objects.create(user=self.user, assistant=self.assistant, conversation_state=[])

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.consumers.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.services.send_chat_message")
    def test_streams_chunks_then_done_and_saves_message(self, mock_send, mock_memories, mock_extract_task, mock_title_task):
        # Mock one level below run_and_broadcast_turn (not that function itself)
        # so the real _record_turn/persistence logic actually runs and can be
        # asserted on.
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

        thread = Thread.objects.get(pk=frames[3]["thread_id"])
        assistant_message = Message.objects.get(thread=thread, sender="assistant")
        self.assertEqual(assistant_message.tool_calls, ["search_memories"])

    @patch('chat_messages.services.generate_thread_title_task')
    @patch('chat_messages.services.extract_memories_task')
    @patch('chat_messages.consumers.retrieve_relevant_memories', return_value=[])
    @patch('chat_messages.services.send_chat_message')
    def test_rejects_second_message_for_the_same_thread_while_a_turn_is_still_in_flight(
        self, mock_send, mock_memories, mock_extract_task, mock_title_task,
    ):
        # Regression test for the per-thread claim (generation_registry.try_claim):
        # without it, two messages for the SAME existing thread arriving
        # close together (a double-click, two tabs) would both pass and spin
        # up two concurrent generations — two LLM calls, two _record_turn
        # calls, credits deducted twice. Generation is now owned by
        # generation_registry, not a per-connection attribute, so this must
        # be tested against a real, already-existing thread_id (a fresh
        # thread_id: None create isn't shared/race-able the same way — see
        # test_rejects_second_new_thread_message_on_the_same_connection below
        # for that, separate, case).
        release_first_call = asyncio.Event()
        entered_first_call = asyncio.Event()

        async def fake_send_chat_message(*args, **kwargs):
            entered_first_call.set()
            await release_first_call.wait()

            async def fake_chunks():
                yield "Done."

            return fake_chunks(), None, False

        mock_send.side_effect = fake_send_chat_message

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"thread_id": self.existing_thread.id, "message": "First"})
            thinking_frame = await communicator.receive_json_from()
            await entered_first_call.wait()

            await communicator.send_json_to({"thread_id": self.existing_thread.id, "message": "Second"})
            rejection_frame = await communicator.receive_json_from()

            release_first_call.set()
            chunk_frame = await communicator.receive_json_from()
            done_frame = await communicator.receive_json_from()

            await communicator.disconnect()
            return thinking_frame, rejection_frame, chunk_frame, done_frame

        thinking_frame, rejection_frame, chunk_frame, done_frame = run(scenario())
        self.assertEqual(thinking_frame, {"status": "thinking"})
        self.assertIn("error", rejection_frame)
        self.assertEqual(chunk_frame, {"chunk": "Done."})
        self.assertTrue(done_frame["done"])
        # send_chat_message was only ever invoked once — the second message
        # was rejected before spawning a competing turn.
        self.assertEqual(mock_send.call_count, 1)

    @patch('chat_messages.services.generate_thread_title_task')
    @patch('chat_messages.services.extract_memories_task')
    @patch('chat_messages.consumers.retrieve_relevant_memories', return_value=[])
    @patch('chat_messages.services.send_chat_message')
    def test_rejects_second_new_thread_message_on_the_same_connection(
        self, mock_send, mock_memories, mock_extract_task, mock_title_task,
    ):
        # A brand-new thread (thread_id: None) has no id yet to claim in
        # generation_registry before the first one is resolved — this is
        # exactly the case self._creating_thread exists to guard, on this
        # one connection, since generation_registry can't.
        release_first_call = asyncio.Event()
        entered_first_call = asyncio.Event()

        async def fake_send_chat_message(*args, **kwargs):
            entered_first_call.set()
            await release_first_call.wait()

            async def fake_chunks():
                yield "Done."

            return fake_chunks(), None, False

        mock_send.side_effect = fake_send_chat_message

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"thread_id": None, "message": "First"})
            thinking_frame = await communicator.receive_json_from()
            await entered_first_call.wait()

            await communicator.send_json_to({"thread_id": None, "message": "Second"})
            rejection_frame = await communicator.receive_json_from()

            release_first_call.set()
            await communicator.receive_json_from()  # chunk
            await communicator.receive_json_from()  # done

            await communicator.disconnect()
            return thinking_frame, rejection_frame

        thinking_frame, rejection_frame = run(scenario())
        self.assertEqual(thinking_frame, {"status": "thinking"})
        self.assertIn("error", rejection_frame)
        self.assertEqual(mock_send.call_count, 1)

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
    @patch("chat_messages.consumers.retrieve_relevant_memories")
    @patch("chat_messages.services.send_chat_message")
    def test_completes_turn_when_memory_retrieval_fails(
        self, mock_send, mock_memories, mock_extract_task, mock_title_task,
    ):
        # Regression test: retrieve_relevant_memories used to be called with
        # no try/except around a fire-and-forget task — an exception there
        # (hit live via a stale/mismatched embedding dimension) killed the
        # task silently, with no chat.error ever reaching the client and the
        # thread's generation_registry claim never released. Memory recall
        # is supplementary, not core, so a failure there must degrade
        # gracefully rather than take down the whole turn.
        mock_memories.side_effect = RuntimeError("vector dimension mismatch")

        async def fake_chunks():
            yield "Hi there."

        mock_send.return_value = (fake_chunks(), None, False)

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"thread_id": None, "message": "Hi"})
            thinking_frame = await communicator.receive_json_from()
            chunk_frame = await communicator.receive_json_from()
            done_frame = await communicator.receive_json_from()

            await communicator.disconnect()
            return thinking_frame, chunk_frame, done_frame

        thinking_frame, chunk_frame, done_frame = run(scenario())
        self.assertEqual(thinking_frame, {"status": "thinking"})
        self.assertEqual(chunk_frame, {"chunk": "Hi there."})
        self.assertTrue(done_frame["done"])
        # memories=[] was passed through despite the retrieval failure.
        self.assertEqual(mock_send.call_args.kwargs["memories"], [])

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.consumers.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.services.send_chat_message", new_callable=AsyncMock)
    @patch("chat_messages.consumers.get_or_create_thread")
    def test_releases_claim_and_sends_error_on_unexpected_failure_before_generation_starts(
        self, mock_get_thread, mock_send, mock_memories, mock_extract_task, mock_title_task,
    ):
        # Regression test: a gap one step earlier than the memory-retrieval
        # incident above — anything unexpected between claiming the thread
        # (try_claim in receive()) and handing off to run_and_broadcast_turn
        # (get_or_create_thread raising something other than
        # Thread.DoesNotExist, group_add hitting a transient Redis blip,
        # etc.) used to propagate out of this un-awaited task uncaught,
        # leaking the generation_registry claim forever — every subsequent
        # message on that thread would be rejected as "still processing"
        # with no way to recover short of a server restart.
        mock_get_thread.side_effect = RuntimeError("db connection blip")

        async def fake_chunks():
            yield "Hi again yourself."

        mock_send.return_value = (fake_chunks(), None, False)

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"thread_id": self.existing_thread.id, "message": "Hi"})
            error_frame = await communicator.receive_json_from()

            # The claim must be released — a second attempt on the same
            # thread should be accepted (not rejected as "still processing"),
            # proving generation_registry no longer thinks a generation is
            # in flight for it.
            mock_get_thread.side_effect = None
            mock_get_thread.return_value = self.existing_thread
            await communicator.send_json_to({"thread_id": self.existing_thread.id, "message": "Hi again"})
            second_frame = await communicator.receive_json_from()

            await communicator.disconnect()
            return error_frame, second_frame

        error_frame, second_frame = run(scenario())
        self.assertEqual(
            error_frame, {"error": "Something went wrong while starting the response. Please try again."},
        )
        # A rejected-as-still-processing claim would arrive as an "error"
        # frame synchronously, before any "thinking" status — getting to
        # "thinking" proves try_claim succeeded, i.e. the earlier claim was
        # actually released.
        self.assertEqual(second_frame, {"status": "thinking"})

    def test_rejects_unowned_project_id_on_new_thread_creation(self):
        # Regression test: get_or_create_thread used to silently drop an
        # unresolvable/unauthorized project_id and create a project-less
        # thread anyway (200-equivalent success) — now it raises
        # Project.DoesNotExist, which must surface as a clean error frame
        # here rather than falling into the generic P0 handler's vague
        # "Something went wrong" message.
        other_user = User.objects.create_user(username="otherprojectowner", password="pass")
        other_project = Project.objects.create(user=other_user, name="Not yours")
        # setUp already creates self.existing_thread for self.user — capture
        # the baseline so we can assert no *new* thread got created, rather
        # than assuming zero.
        threads_before = Thread.objects.filter(user=self.user).count()

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to(
                {"thread_id": None, "message": "Hi", "project_id": other_project.id},
            )
            error_frame = await communicator.receive_json_from()
            await communicator.disconnect()
            return error_frame

        error_frame = run(scenario())
        self.assertEqual(error_frame, {"error": "Project not found."})
        self.assertEqual(Thread.objects.filter(user=self.user).count(), threads_before)

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

            await communicator.send_json_to(
                {"type": "tool_confirmation", "thread_id": confirm_frame["thread_id"], "confirmed": True},
            )
            chunk_frame = await communicator.receive_json_from()
            done_frame = await communicator.receive_json_from()

            await communicator.disconnect()
            return thinking_frame, confirm_frame, chunk_frame, done_frame

        thinking_frame, confirm_frame, chunk_frame, done_frame = run(scenario())
        self.assertEqual(thinking_frame, {"status": "thinking"})
        self.assertEqual(confirm_frame["status"], "confirm_required")
        self.assertEqual(confirm_frame["tool"], "delegate_to_model")
        self.assertEqual(confirm_frame["arguments"], {"provider": "gemini"})
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
            confirm_frame = await communicator.receive_json_from()

            await communicator.send_json_to(
                {"type": "tool_confirmation", "thread_id": confirm_frame["thread_id"], "confirmed": False},
            )
            chunk_frame = await communicator.receive_json_from()

            await communicator.disconnect()
            return chunk_frame

        chunk_frame = run(scenario())
        self.assertEqual(chunk_frame, {"chunk": "Declined."})

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.consumers.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.services.send_chat_message")
    def test_generation_survives_disconnect_and_still_persists(
        self, mock_send, mock_memories, mock_extract_task, mock_title_task,
    ):
        # Core regression test for the incident this whole change exists to
        # fix: a dropped connection must not cancel the in-flight generation
        # — it keeps running, and the message still gets saved, even though
        # nobody is listening anymore by the time it finishes.
        release_call = asyncio.Event()

        async def fake_send_chat_message(*args, **kwargs):
            await release_call.wait()

            async def fake_chunks():
                yield "Still here!"

            return fake_chunks(), None, False

        mock_send.side_effect = fake_send_chat_message

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"thread_id": self.existing_thread.id, "message": "Hi"})
            await communicator.receive_json_from()  # thinking

            # attach_task() runs slightly after the "thinking" frame is sent
            # (see _start_generation) — poll briefly rather than assuming
            # it's already there the instant "thinking" is received.
            task = None
            for _ in range(20):
                gen = generation_registry._active.get(self.existing_thread.id)
                if gen is not None and gen.task is not None:
                    task = gen.task
                    break
                await asyncio.sleep(0)
            assert task is not None, "generation task was never registered"

            await communicator.disconnect()

            # The whole point: disconnect() must not have cancelled it.
            assert not task.cancelled()
            assert not task.done()

            release_call.set()
            await task  # nobody is listening anymore — it must still finish cleanly

        run(scenario())

        self.assertEqual(Message.objects.filter(thread=self.existing_thread).count(), 2)
        self.existing_thread.refresh_from_db()
        self.assertEqual(
            self.existing_thread.conversation_state[-1],
            {"role": "assistant", "content": "Still here!"},
        )
        # The registry entry must be released once the task completes, or a
        # later message on this thread would be rejected forever.
        self.assertFalse(generation_registry.is_active(self.existing_thread.id))

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.consumers.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.services.send_chat_message")
    def test_second_connection_can_join_and_receive_live_chunks(
        self, mock_send, mock_memories, mock_extract_task, mock_title_task,
    ):
        release_call = asyncio.Event()

        async def fake_send_chat_message(*args, **kwargs):
            await release_call.wait()

            async def fake_chunks():
                yield "Live chunk"

            return fake_chunks(), None, False

        mock_send.side_effect = fake_send_chat_message

        async def scenario():
            first = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            first.scope["user"] = self.user
            connected, _ = await first.connect()
            assert connected

            await first.send_json_to({"thread_id": self.existing_thread.id, "message": "Hi"})
            await first.receive_json_from()  # thinking

            second = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            second.scope["user"] = self.user
            connected2, _ = await second.connect()
            assert connected2

            await second.send_json_to({"type": "join_thread", "thread_id": self.existing_thread.id})
            resuming_frame = await second.receive_json_from()

            release_call.set()
            first_chunk = await first.receive_json_from()
            second_chunk = await second.receive_json_from()
            first_done = await first.receive_json_from()
            second_done = await second.receive_json_from()

            await first.disconnect()
            await second.disconnect()
            return resuming_frame, first_chunk, second_chunk, first_done, second_done

        resuming_frame, first_chunk, second_chunk, first_done, second_done = run(scenario())
        self.assertEqual(resuming_frame, {"status": "resuming"})
        self.assertEqual(first_chunk, {"chunk": "Live chunk"})
        self.assertEqual(second_chunk, {"chunk": "Live chunk"})
        self.assertTrue(first_done["done"])
        self.assertTrue(second_done["done"])

    def test_join_thread_rejects_a_thread_belonging_to_another_user(self):
        other_user = User.objects.create_user(username="otheruser", password="pass")

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = other_user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"type": "join_thread", "thread_id": self.existing_thread.id})
            frame = await communicator.receive_json_from()
            await communicator.disconnect()
            return frame

        frame = run(scenario())
        self.assertIn("error", frame)

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.consumers.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.services.send_chat_message")
    def test_join_thread_resurfaces_pending_confirmation(
        self, mock_send, mock_memories, mock_extract_task, mock_title_task,
    ):
        # A reconnecting client (or a second tab) must be able to actually
        # answer a confirmation that was broadcast before it joined, not
        # just be told "resuming" with no way to act on it.
        async def fake_send_chat_message(*args, **kwargs):
            confirmed = await kwargs["confirm_tool_call"]("delegate_to_model", {"provider": "gemini"})

            async def fake_chunks():
                yield "Confirmed!" if confirmed else "Declined."

            return fake_chunks(), None, False

        mock_send.side_effect = fake_send_chat_message

        async def scenario():
            first = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            first.scope["user"] = self.user
            connected, _ = await first.connect()
            assert connected

            await first.send_json_to({"thread_id": self.existing_thread.id, "message": "Hi"})
            await first.receive_json_from()  # thinking
            await first.receive_json_from()  # confirm_required (first delivery)

            second = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            second.scope["user"] = self.user
            connected2, _ = await second.connect()
            assert connected2

            await second.send_json_to({"type": "join_thread", "thread_id": self.existing_thread.id})
            resurfaced_frame = await second.receive_json_from()

            await second.send_json_to(
                {"type": "tool_confirmation", "thread_id": resurfaced_frame["thread_id"], "confirmed": True},
            )
            first_chunk = await first.receive_json_from()

            await first.disconnect()
            await second.disconnect()
            return resurfaced_frame, first_chunk

        resurfaced_frame, first_chunk = run(scenario())
        self.assertEqual(resurfaced_frame["status"], "confirm_required")
        self.assertEqual(resurfaced_frame["tool"], "delegate_to_model")
        self.assertEqual(resurfaced_frame["arguments"], {"provider": "gemini"})
        self.assertEqual(first_chunk, {"chunk": "Confirmed!"})

    @patch("chat_messages.services.generate_thread_title_task")
    @patch("chat_messages.services.extract_memories_task")
    @patch("chat_messages.consumers.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.services.send_chat_message")
    def test_stop_generation_saves_partial_text_and_unblocks_thread(
        self, mock_send, mock_memories, mock_extract_task, mock_title_task,
    ):
        async def fake_send_chat_message(*args, **kwargs):
            async def fake_chunks():
                yield "Hello"
                # Blocks "generating" indefinitely — stop_generation must
                # interrupt this, not wait it out.
                await asyncio.Event().wait()

            return fake_chunks(), None, False

        mock_send.side_effect = fake_send_chat_message

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = self.user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"thread_id": self.existing_thread.id, "message": "Hi"})
            await communicator.receive_json_from()  # thinking
            chunk_frame = await communicator.receive_json_from()  # "Hello"

            await communicator.send_json_to({"type": "stop_generation", "thread_id": self.existing_thread.id})
            done_frame = await communicator.receive_json_from()

            await communicator.disconnect()
            return chunk_frame, done_frame

        chunk_frame, done_frame = run(scenario())
        self.assertEqual(chunk_frame, {"chunk": "Hello"})
        self.assertEqual(done_frame, {"done": True, "thread_id": self.existing_thread.id, "stopped": True})

        self.assertEqual(Message.objects.filter(thread=self.existing_thread).count(), 2)
        self.existing_thread.refresh_from_db()
        self.assertEqual(
            self.existing_thread.conversation_state[-1],
            {"role": "assistant", "content": "Hello"},
        )
        # The registry entry must be released, or a later message on this
        # thread would be rejected forever.
        self.assertFalse(generation_registry.is_active(self.existing_thread.id))

    def test_stop_generation_rejects_a_thread_belonging_to_another_user(self):
        other_user = User.objects.create_user(username="otheruser2", password="pass")

        async def scenario():
            communicator = WebsocketCommunicator(ConversationConsumer.as_asgi(), "/ws/conversations/")
            communicator.scope["user"] = other_user
            connected, _ = await communicator.connect()
            assert connected

            await communicator.send_json_to({"type": "stop_generation", "thread_id": self.existing_thread.id})
            frame = await communicator.receive_json_from()
            await communicator.disconnect()
            return frame

        frame = run(scenario())
        self.assertIn("error", frame)

    def test_call_cleans_up_group_membership_even_if_dispatch_crashes(self):
        # Channels' own dispatch loop only catches StopConsumer — any other
        # exception (e.g. the transient Redis read-timeout crash observed in
        # production) bypasses websocket.disconnect entirely, so
        # disconnect()'s own cleanup never runs. __call__'s finally block is
        # the safety net for that path specifically.
        consumer = ConversationConsumer()
        consumer.scope = {"user": self.user}
        consumer.channel_layer = AsyncMock()
        consumer.channel_name = "test-channel-x"

        async def boom(*args, **kwargs):
            consumer._joined_groups.add("thread_999")
            raise RuntimeError("simulated crash in channel-layer dispatch")

        async def scenario():
            with patch.object(AsyncConsumer, "__call__", boom):
                with self.assertRaises(RuntimeError):
                    await consumer.__call__({}, None, None)

        run(scenario())

        consumer.channel_layer.group_discard.assert_awaited_once_with("thread_999", "test-channel-x")
        self.assertEqual(consumer._joined_groups, set())


class SendMessageAPICreditsTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="apiuser", password="pass")
        self.client.force_authenticate(user=self.user)

    @patch("chat_messages.views.retrieve_relevant_memories", return_value=[])
    @patch("chat_messages.views.send_message", new_callable=AsyncMock)
    def test_returns_402_on_insufficient_credits(self, mock_send, mock_memories):
        mock_send.side_effect = InsufficientCreditsError("Crédit épuisé.")

        response = self.client.post(reverse('message-list-create-thread'), {"message": "Hi"}, format='json')

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(response.data["error"], "Crédit épuisé.")

    def test_returns_404_for_unowned_project_id(self):
        # Regression test: an unresolvable/unauthorized project_id used to
        # be silently dropped by get_or_create_thread, creating a
        # project-less thread and returning 200 — no signal the caller's
        # intended project association had failed.
        other_user = User.objects.create_user(username="otherprojectowner2", password="pass")
        other_project = Project.objects.create(user=other_user, name="Not yours")

        response = self.client.post(
            reverse('message-list-create-thread'), {"message": "Hi", "project_id": other_project.id}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"], "Project not found.")
        self.assertEqual(Thread.objects.filter(user=self.user).count(), 0)
