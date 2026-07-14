# chat_messages/consumers.py
import asyncio
import json
import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from chat_messages import generation_registry
from chat_messages.services import run_and_broadcast_turn
from librarian.services import retrieve_relevant_memories
from threads.models import Thread
from threads.services import get_or_create_thread

logger = logging.getLogger(__name__)


class ConversationConsumer(AsyncWebsocketConsumer):
    async def __call__(self, scope, receive, send):
        # Channels' own dispatch loop (AsyncConsumer.__call__) only catches
        # StopConsumer — any other exception (e.g. a transient Redis read
        # timeout crashing the channel-layer listen loop, observed
        # repeatedly in production) propagates straight out, which means
        # disconnect() below is *never called* for that exit path: it's only
        # invoked in response to a proper websocket.disconnect ASGI message,
        # which a crash bypasses entirely. Without this, a crashed
        # connection's group memberships never get cleaned up and linger
        # until Channels' own group_expiry (24h). Set unconditionally here
        # (not in connect()) so it exists even if the crash happens before
        # connect() ever runs.
        self._joined_groups: set[str] = set()
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Safety net, not the primary path — disconnect() already does
            # this on a clean disconnect, and this is then a no-op (empty
            # set) by the time it runs.
            for group_name in self._joined_groups:
                await self.channel_layer.group_discard(group_name, self.channel_name)
            self._joined_groups.clear()

    async def connect(self):
        if self.scope["user"].is_anonymous:
            await self.close(code=4001)
            return
        # Guards a rapid double-send of a brand-new thread (thread_id: None)
        # on THIS connection — generation_registry can't protect that case
        # itself (nothing to key a claim on before a thread exists, and each
        # such send would create its own independent thread anyway). This is
        # the one piece of the old self._active_task's job that moving the
        # rest of the gate into generation_registry doesn't cover.
        self._creating_thread = False
        await self.accept()

    async def disconnect(self, close_code):
        for group_name in self._joined_groups:
            await self.channel_layer.group_discard(group_name, self.channel_name)
        self._joined_groups.clear()
        # Deliberately no task cancellation here. An in-flight generation
        # (if any) is owned by generation_registry, not this connection —
        # it keeps running to completion regardless of this connection
        # dropping. That's the entire point: a transient network/Redis blip
        # killing this connection must not lose the response or the credit
        # deduction that goes with it.

    async def receive(self, text_data):
        """
        Expects JSON: {"thread_id": int|null, "message": str, "ai_provider"?: str, "model"?: str, "project_id"?: int}
        `ai_provider`/`model`/`project_id` are only consulted when `thread_id` is null (creation
        time) — an existing thread's provider/model is read fresh from the DB on every send, so a
        PATCH to /api/threads/<id>/ takes effect on the next message with no extra wiring.

        Generation runs as a task owned by generation_registry (not this
        connection) and broadcasts every frame — {"chunk": ...}, {"status": ...},
        {"done": true, "thread_id": int}, {"error": ...} — to a per-thread
        Channels group, so it survives this connection dropping and any
        connection currently in the group (a reconnect, a second tab) sees
        the same frames a solo connection would have seen directly.

        A turn can pause mid-stream waiting on user approval for a sensitive tool
        (e.g. delegate_to_model): {"status": "confirm_required", "tool": ..., "arguments": ...,
        "thread_id": ...} is broadcast, and a client replies (on any connection
        attached to the thread's group) with
        {"type": "tool_confirmation", "thread_id": int, "confirmed": bool}.

        A client that already knows a thread_id (e.g. on page load/reconnect)
        should send {"type": "join_thread", "thread_id": int} to attach to
        that thread's group and find out whether a generation is already in
        flight for it, without starting a new one.

        {"type": "stop_generation", "thread_id": int} cancels an in-flight
        generation for that thread. Whatever was already generated is saved
        (see run_and_broadcast_turn's CancelledError handling) and the group
        gets a {"done": true, "thread_id": int, "stopped": true} frame.
        """
        data = json.loads(text_data)
        msg_type = data.get("type")

        if msg_type == "tool_confirmation":
            future = generation_registry.get_confirmation_future(data.get("thread_id"))
            if future is not None and not future.done():
                future.set_result(bool(data.get("confirmed")))
            return

        if msg_type == "join_thread":
            await self._join_thread(data.get("thread_id"))
            return

        if msg_type == "stop_generation":
            await self._stop_generation(data.get("thread_id"))
            return

        # New chat message (thread_id may be None for a brand-new thread).
        # try_claim/the _creating_thread check below must stay directly here,
        # synchronous, with no `await` before asyncio.create_task — that's
        # what makes the claim atomic with respect to every other coroutine
        # in the process (asyncio only switches tasks at an await/yield
        # point). Moving this into an awaited helper would silently reopen
        # the double-generation race it exists to close.
        thread_id = data.get("thread_id")
        if thread_id is not None:
            if not generation_registry.try_claim(thread_id):
                await self._safe_send({"error": "A previous message is still being processed."})
                return
        else:
            if self._creating_thread:
                await self._safe_send({"error": "A previous message is still being processed."})
                return
            self._creating_thread = True

        asyncio.create_task(self._start_generation(data, thread_id))

    async def _join_thread(self, thread_id) -> None:
        if thread_id is None:
            return
        user = self.scope["user"]
        try:
            # Same ownership-scoped lookup used for a normal message
            # (threads/services.py::get_or_create_thread does
            # Thread.objects.get(pk=thread_id, user=user) when thread_id is
            # given) — a thread_id belonging to a different user raises
            # Thread.DoesNotExist here too. Without this check, any
            # authenticated user could join_thread on someone else's
            # thread_id and start receiving their chunks/tool-call
            # arguments/confirmation prompts.
            await sync_to_async(get_or_create_thread)(user, thread_id=thread_id)
        except Thread.DoesNotExist:
            await self._safe_send({"error": "Thread not found."})
            return

        group_name = f"thread_{thread_id}"
        await self.channel_layer.group_add(group_name, self.channel_name)
        self._joined_groups.add(group_name)
        if not generation_registry.is_active(thread_id):
            return
        pending = generation_registry.get_pending_confirmation(thread_id)
        if pending is not None:
            # Re-send the same confirm_required prompt rather than a
            # generic "resuming" — the original broadcast may have gone out
            # while nobody (or a connection that's since dropped) was
            # listening, and without this a client that reconnects has no
            # way to actually answer it before the timeout.
            await self._safe_send({
                "status": "confirm_required",
                "tool": pending.tool,
                "arguments": pending.arguments,
                "thread_id": thread_id,
            })
        else:
            await self._safe_send({"status": "resuming"})

    async def _stop_generation(self, thread_id) -> None:
        if thread_id is None:
            return
        user = self.scope["user"]
        try:
            # Same ownership check as _join_thread — without it, any
            # authenticated user could cancel someone else's generation by
            # guessing/sending a thread_id.
            await sync_to_async(get_or_create_thread)(user, thread_id=thread_id)
        except Thread.DoesNotExist:
            await self._safe_send({"error": "Thread not found."})
            return
        task = generation_registry.get_task(thread_id)
        if task is not None and not task.done():
            task.cancel()

    async def _safe_send(self, payload: dict) -> None:
        """The transport can already be gone by the time we try to report an
        error (e.g. a mid-stream network/Redis blip killed it) — that send
        would itself raise, producing a second, noisier traceback for the
        same underlying failure with nothing left to do about it. Swallow
        that specific case rather than letting it propagate."""
        try:
            await self.send(json.dumps(payload))
        except Exception:
            logger.warning("Could not send WS frame, connection likely already closed: %s", payload)

    async def _start_generation(self, data, thread_id):
        """Resolves the thread, joins its group, and spawns+registers the
        actual generation task — deliberately does not await it. The task
        (chat_messages.services.run_and_broadcast_turn) outlives this method
        and this connection."""
        message_text = data.get("message")
        ai_provider = data.get("ai_provider")
        model = data.get("model")
        project_id = data.get("project_id")

        if not message_text:
            if thread_id is not None:
                generation_registry.release(thread_id)
            self._creating_thread = False
            await self._safe_send({"error": "Missing fields"})
            return

        user = self.scope["user"]
        # Tracks which generation_registry key (if any) is currently claimed
        # by this call, so the outer except below always releases the right
        # one — an existing thread is already claimed under thread_id before
        # this method even runs; a brand-new thread isn't claimed until
        # try_claim(thread.id) succeeds a few lines down.
        claimed_thread_id = thread_id
        try:
            try:
                thread = await sync_to_async(get_or_create_thread)(
                    user, thread_id=thread_id, ai_provider=ai_provider, model=model, project_id=project_id,
                )
            except Thread.DoesNotExist:
                if thread_id is not None:
                    generation_registry.release(thread_id)
                await self._safe_send({"error": "Thread not found."})
                return

            if thread_id is None:
                # Brand-new thread: claim now using the just-assigned id. No
                # race to worry about — nothing else can reference this id
                # before this line runs.
                generation_registry.try_claim(thread.id)
                claimed_thread_id = thread.id

            # Register this method's own task (asyncio.create_task(self._start_generation(...))
            # in receive()) rather than spawning a further nested task for
            # run_and_broadcast_turn — one task, awaited directly below, is
            # simpler and just as uncancellable-by-disconnect as two would be
            # (nothing cancels either), and it's what a future
            # cancel/"stop generation" feature would target.
            generation_registry.attach_task(thread.id, asyncio.current_task())

            group_name = f"thread_{thread.id}"
            await self.channel_layer.group_add(group_name, self.channel_name)
            self._joined_groups.add(group_name)

            try:
                memories = await sync_to_async(retrieve_relevant_memories)(user, message_text)
            except Exception:
                # Memory recall is a supplementary enrichment, not the core
                # feature — degrade gracefully (e.g. a corrupted/mismatched
                # embedding row for this user) rather than losing the whole
                # turn to an unhandled task exception, which previously left
                # the thread claimed forever in generation_registry with no
                # chat.error ever reaching the client (silent, permanent hang).
                logger.exception("Failed to retrieve memories for user %s; continuing without them", user.id)
                memories = []
            await self.channel_layer.group_send(group_name, {"type": "chat.status", "status": "thinking"})

            await run_and_broadcast_turn(thread, message_text, user, group_name, memories=memories)
        except Exception:
            # Anything else unexpected between claiming the thread and
            # handing off to run_and_broadcast_turn (whose own try/finally
            # already covers itself once it starts) must not die silently
            # inside this un-awaited task (asyncio.create_task in receive(),
            # never awaited by anything) — that previously left
            # generation_registry's claim leaked forever with no chat.error
            # ever reaching the client (the same failure mode as the
            # retrieve_relevant_memories incident above, just one step
            # earlier in this method — e.g. get_or_create_thread raising
            # something other than Thread.DoesNotExist, or a transient
            # Redis blip on group_add, both previously uncaught here).
            logger.exception("Unexpected failure in _start_generation for thread %s", claimed_thread_id)
            if claimed_thread_id is not None:
                generation_registry.release(claimed_thread_id)
            await self._safe_send({"error": "Something went wrong while starting the response. Please try again."})
        finally:
            # generation_registry.release() already happened above (or
            # inside run_and_broadcast_turn's own finally on the success
            # path) — this only resets the connection-local new-thread
            # guard, a separate concern.
            if thread_id is None:
                self._creating_thread = False

    # --- Channels group event handlers ---
    # Channels maps a broadcast event's "type" (dots replaced with
    # underscores) to a method here automatically, e.g. "chat.chunk" -> chat_chunk.
    # These just forward to whichever connections are currently in the
    # group, via the already-hardened _safe_send.

    async def chat_chunk(self, event):
        await self._safe_send({"chunk": event["chunk"]})

    async def chat_status(self, event):
        payload = {"status": event["status"]}
        if "tool" in event:
            payload["tool"] = event["tool"]
        if "provider" in event:
            payload["provider"] = event["provider"]
        await self._safe_send(payload)

    async def chat_confirm_required(self, event):
        await self._safe_send({
            "status": "confirm_required",
            "tool": event["tool"],
            "arguments": event["arguments"],
            "thread_id": event["thread_id"],
        })

    async def chat_done(self, event):
        payload = {"done": True, "thread_id": event["thread_id"]}
        if event.get("stopped"):
            payload["stopped"] = True
        await self._safe_send(payload)

    async def chat_error(self, event):
        await self._safe_send({"error": event["error"]})
