# chat_messages/consumers.py
import asyncio
import json
import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from chat_messages import generation_registry
from chat_messages.models import PendingTurn
from core.rate_limit import check_rate_limit
from chat_messages.services import run_and_broadcast_turn
from librarian.services import retrieve_relevant_memories
from projects.models import Project
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
            await self._confirm_tool(data.get("thread_id"), bool(data.get("confirmed")))
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
        #
        # Rate-limited before any of that — Django's cache framework is
        # synchronous but not I/O-bound (in-process for LocMemCache under
        # this app's current single-process deployment), so it's safe to
        # call directly here without breaking the synchronous-claim
        # invariant above. Same per-minute budget as SendMessageAPIView's
        # HTTP equivalent (settings.CHAT_MESSAGES_PER_MINUTE) — this is the
        # WS side of the same underlying "send a chat message" action, and
        # nothing else bounds tool-heavy or just-enthusiastic message bursts
        # from hammering the shared global-provider-key rate limit.
        user = self.scope["user"]
        if not check_rate_limit(f"ratelimit:chat:{user.id}", settings.CHAT_MESSAGES_PER_MINUTE, 60):
            await self._safe_send({"error": "You're sending messages too fast. Please slow down."})
            return

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
            thread = await sync_to_async(get_or_create_thread)(user, thread_id=thread_id)
        except Thread.DoesNotExist:
            await self._safe_send({"error": "Thread not found."})
            return

        group_name = f"thread_{thread_id}"
        await self.channel_layer.group_add(group_name, self.channel_name)
        self._joined_groups.add(group_name)
        if not generation_registry.is_active(thread_id):
            # generation_registry is purely in-memory, so it's always empty
            # right after a process restart — a PendingTurn row surviving
            # that (see its docstring) means a turn on this thread was
            # interrupted at some point (mid-stream, mid-tool-call,
            # mid-confirmation-wait, anywhere) and, since nothing is
            # persisted to Message until a turn completes, is otherwise gone
            # without a trace. Still not *exact-point* resume (a provider's
            # native response object isn't serializable, so we can't
            # reconstruct mid-tool-loop state) — but when nothing could have
            # had a side effect yet (no tool was ever proposed), it's safe
            # to replay the whole turn automatically instead of just telling
            # the client to resend it themselves.
            stale = await sync_to_async(PendingTurn.objects.filter(thread_id=thread_id).first)()
            if stale is not None:
                # Scoped to stale.pk, not thread_id -- an unscoped delete
                # could wipe a *different*, concurrently-created turn's own
                # row (e.g. another tab's send winning a race right after
                # this fetch) instead of just the one actually found stale.
                # Same reasoning as _record_turn's pk-scoped deletes.
                await sync_to_async(PendingTurn.objects.filter(pk=stale.pk).delete)()
                if stale.tool_calls:
                    # A tool was proposed this turn -- on_tool_call fires
                    # before a tool's confirmation gate (see AgentTool's
                    # docstring), so this can't tell "actually ran" apart
                    # from "merely proposed, maybe declined". Deliberately
                    # conservative: don't risk auto-replaying a tool call
                    # that may have already had a real side effect.
                    await self._safe_send({
                        "error": (
                            "Your previous request was interrupted, and a tool call may have "
                            "already run before that happened. Check before sending it again "
                            "rather than assuming nothing happened."
                        ),
                        "thread_id": thread_id,
                    })
                    return
                # No tool was ever proposed this turn, so nothing could have
                # had a side effect -- safe to resend the original message
                # ourselves rather than making the user do it. Reuses the
                # existing "resuming" frame shape verbatim: the frontend
                # already re-inserts user_text as a message bubble and shows
                # the resuming indicator for this exact shape, so the normal
                # chat.status/chat.chunk frames the newly spawned task emits
                # take over seamlessly, no frontend changes needed.
                #
                # Same rate limit as every other turn-start path (receive()'s
                # own check before _start_generation) -- this still triggers
                # a real provider call against the same shared budget, even
                # though the user isn't the one directly initiating it.
                if not check_rate_limit(f"ratelimit:chat:{user.id}", settings.CHAT_MESSAGES_PER_MINUTE, 60):
                    await self._safe_send({
                        "error": (
                            "Your previous request was interrupted before it could complete. "
                            "Please send it again."
                        ),
                        "thread_id": thread_id,
                    })
                    return
                if not generation_registry.try_claim(thread_id):
                    # Not actually rare: two tabs on the same thread both
                    # reconnecting after a restart can both get past the
                    # is_active() check above (there are several awaited DB
                    # calls in between) and race here. The loser must not
                    # return silently — by this point the winner's turn is
                    # registered in generation_registry, so this is exactly
                    # the "already active" case below; send the same status
                    # a plain mid-stream rejoin would get instead of leaving
                    # this tab with no frame at all.
                    await self._send_active_turn_status(thread_id)
                    return
                # Set synchronously, right after the claim and before any
                # further await, so a third party racing in right behind us
                # (another tab's _join_thread, or this same one on a
                # subsequent call) sees the real text via get_turn_progress
                # immediately -- not the blank default try_claim seeds
                # _Generation with, which would otherwise be visible until
                # _run_turn_task's own task actually gets scheduled and sets
                # this itself moments later.
                generation_registry.set_turn_text(thread_id, stale.user_text)
                await self._safe_send({
                    "status": "resuming", "thread_id": thread_id,
                    "user_text": stale.user_text, "streamed_text": "",
                })
                asyncio.create_task(self._run_turn_task(thread, stale.user_text, user, group_name))
            return
        await self._send_active_turn_status(thread_id)

    async def _send_active_turn_status(self, thread_id) -> None:
        # Nothing is persisted to the DB mid-turn, so this pair is the only
        # record of what's happened so far — without it, a client that
        # (re)joins mid-stream (navigated to another thread and back while
        # this one was still generating, or lost a claim race in
        # _join_thread just above) would see neither the question nor the
        # answer-so-far until the whole turn eventually finishes.
        user_text, streamed_text = generation_registry.get_turn_progress(thread_id)
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
                "source": pending.source,
                "after_file_read": pending.after_file_read,
                "thread_id": thread_id,
                "user_text": user_text,
                "streamed_text": streamed_text,
            })
        else:
            await self._safe_send({
                "status": "resuming",
                "thread_id": thread_id,
                "user_text": user_text,
                "streamed_text": streamed_text,
            })

    async def _confirm_tool(self, thread_id, confirmed: bool) -> None:
        if thread_id is None:
            return
        user = self.scope["user"]
        try:
            # Same ownership check as _join_thread/_stop_generation —
            # without it, any authenticated user could resolve another
            # user's pending tool confirmation (e.g. approve/deny a
            # delegate_to_model escalation on someone else's thread) just by
            # guessing/enumerating thread_id, since generation_registry is
            # keyed only by a plain integer with no ownership check of its
            # own.
            await sync_to_async(get_or_create_thread)(user, thread_id=thread_id)
        except Thread.DoesNotExist:
            await self._safe_send({"error": "Thread not found."})
            return
        future = generation_registry.get_confirmation_future(thread_id)
        if future is not None and not future.done():
            future.set_result(confirmed)

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
        """Resolves the thread, then hands off to _run_turn_task — deliberately
        does not await *that* method's own internal work any differently than
        a plain nested call (see its docstring for why that's still safe)."""
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
        # Tracks whichever generation_registry key is actually claimed at
        # any point in this method, so the outer except below always
        # releases the right one — starts as the caller's own thread_id
        # (already claimed in receive() for an existing thread), updated to
        # thread.id the moment a brand-new thread's own claim succeeds.
        # _run_turn_task's own except (always thread.id, since by the time
        # it runs `thread` is all there is) covers the realistic failure
        # modes (e.g. a Redis blip on group_add); this is defense-in-depth
        # for the far less likely case of that handler itself raising.
        resolved_thread_id = thread_id
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
            except Project.DoesNotExist:
                if thread_id is not None:
                    generation_registry.release(thread_id)
                await self._safe_send({"error": "Project not found."})
                return

            if thread_id is None:
                # Brand-new thread: claim now using the just-assigned id. No
                # race to worry about — nothing else can reference this id
                # before this line runs.
                generation_registry.try_claim(thread.id)
            resolved_thread_id = thread.id

            group_name = f"thread_{thread.id}"
            await self._run_turn_task(thread, message_text, user, group_name)
        except Exception:
            # Anything unexpected resolving the thread itself (not
            # Thread.DoesNotExist/Project.DoesNotExist, e.g. a transient DB
            # blip) must not die silently inside this un-awaited task
            # (asyncio.create_task in receive(), never awaited by anything)
            # — that would leak generation_registry's claim forever with no
            # chat.error ever reaching the client. _run_turn_task's own
            # try/except already covers everything from thread resolution
            # onward; this is the one step still outside it.
            logger.exception("Unexpected failure in _start_generation for thread %s", resolved_thread_id)
            if resolved_thread_id is not None:
                generation_registry.release(resolved_thread_id)
            await self._safe_send({"error": "Something went wrong while starting the response. Please try again."})
        finally:
            # Only this call site's own new-thread creation guard --
            # _join_thread's auto-replay path never touches it.
            if thread_id is None:
                self._creating_thread = False

    async def _run_turn_task(self, thread, message_text, user, group_name):
        """Registers, runs, and safety-nets one turn's generation — shared by
        _start_generation (a fresh message; runs as *that* method's own task,
        asyncio.create_task(self._start_generation(...)) in receive()) and
        _join_thread's auto-replay path (a reconnecting client finding a
        stale, safe-to-replay PendingTurn; spawned via its own
        asyncio.create_task there, since _join_thread itself must return
        promptly). Either way, asyncio.current_task() below resolves to
        whichever task is actually running this call, which is what
        generation_registry.attach_task needs to register.

        Deliberately one task, not a further nested one for
        run_and_broadcast_turn itself — simpler, and just as
        uncancellable-by-disconnect as two would be (nothing cancels
        either), and it's what a future cancel/"stop generation" feature
        would target."""
        try:
            generation_registry.attach_task(thread.id, asyncio.current_task())
            generation_registry.set_turn_text(thread.id, message_text)

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
            await self.channel_layer.group_send(
                group_name, {"type": "chat.status", "status": "thinking", "thread_id": thread.id},
            )

            await run_and_broadcast_turn(thread, message_text, user, group_name, memories=memories)
        except asyncio.CancelledError:
            # _stop_generation cancels exactly the task attach_task just
            # registered above -- reachable the instant that line runs, well
            # before run_and_broadcast_turn (whose own CancelledError
            # handling only covers *its own* try, starting later) even gets
            # called. CancelledError is BaseException, not Exception, since
            # Python 3.8 -- needs its own branch, or a stop landing during
            # group_add/memory-retrieval/the "thinking" broadcast would
            # propagate uncaught and leak the claim forever, the same
            # permanent-hang failure mode the except Exception below exists
            # to prevent. A deliberate stop, not a failure: no error frame,
            # just the same chat.done(stopped=True) shape
            # run_and_broadcast_turn's own stop path sends. Nothing was ever
            # streamed yet at this point, so there's nothing to save.
            generation_registry.release(thread.id)
            await self.channel_layer.group_send(
                group_name, {"type": "chat.done", "thread_id": thread.id, "stopped": True},
            )
        except Exception:
            # Covers everything from the registry registration/group_add
            # setup above through run_and_broadcast_turn's own handoff
            # (whose own try/finally already covers itself once it starts)
            # — e.g. a transient Redis blip on group_add. Must not die
            # silently inside this un-awaited task (both call sites use
            # asyncio.create_task, never awaited by anything): that would
            # leak generation_registry's claim forever with no chat.error
            # ever reaching the client. Always releases thread.id, the one
            # key this whole method operates under regardless of which
            # caller spawned it — no separate "which id did we actually
            # claim" bookkeeping needed here.
            logger.exception("Unexpected failure in _run_turn_task for thread %s", thread.id)
            generation_registry.release(thread.id)
            await self._safe_send({"error": "Something went wrong while starting the response. Please try again."})

    # --- Channels group event handlers ---
    # Channels maps a broadcast event's "type" (dots replaced with
    # underscores) to a method here automatically, e.g. "chat.chunk" -> chat_chunk.
    # These just forward to whichever connections are currently in the
    # group, via the already-hardened _safe_send.

    async def chat_chunk(self, event):
        await self._safe_send({"chunk": event["chunk"], "thread_id": event["thread_id"]})

    async def chat_status(self, event):
        payload = {"status": event["status"], "thread_id": event["thread_id"]}
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
            "source": event["source"],
            "after_file_read": event["after_file_read"],
            "thread_id": event["thread_id"],
        })

    async def chat_done(self, event):
        payload = {"done": True, "thread_id": event["thread_id"]}
        if event.get("stopped"):
            payload["stopped"] = True
        await self._safe_send(payload)

    async def chat_error(self, event):
        await self._safe_send({"error": event["error"], "thread_id": event["thread_id"]})
