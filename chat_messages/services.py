import asyncio
import logging
from decimal import Decimal

from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import Sum

from ai_providers.chat_router import (
    FILE_SEARCH_TOOL, InsufficientCreditsError, send_chat_message, deduct_credits, compute_turn_cost_usd,
)
from chat_messages.models import Message, PendingTurn
from librarian.tasks import extract_memories_task
from threads.models import Thread
from threads.tasks import generate_thread_title_task

logger = logging.getLogger(__name__)

# Plain constant, matching the style of e.g. chat_router.py's
# CREDIT_VALUE_USD — not environment-specific, no reason for this to be a
# config() value. An abandoned tool confirmation (everyone disconnected
# while a delegate_to_model approval was pending) is treated as declined
# after this long, so the task/generation_registry entry can't hang forever.
CONFIRMATION_TIMEOUT_SECONDS = 300


def _record_turn(
    thread, history, user_text, assistant_text, tool_calls=None, usage=None, used_global_key=True,
    pending_turn_id=None,
):
    # CAUTION: any future PendingTurn read/update/delete anywhere in this
    # file or consumers.py must be scoped by pk, never by thread_id alone --
    # generation_registry.try_claim only guarantees one *legitimate* WS turn
    # per thread, not one *row* (a crash can leave an orphan, and the HTTP
    # send_message path below shares no locking with the WS path at all). A
    # thread_id-scoped delete/update can silently touch a different turn's
    # own row. This bit twice in 2026-09 review (once in this function, once
    # in consumers.py's _join_thread) before being scoped correctly
    # everywhere -- if you add a new PendingTurn query, grep for
    # `PendingTurn.objects` first and match the pk-scoping pattern already
    # used by every other one.
    #
    # BYOK spend isn't deducted from credits at all (see the used_global_key
    # gate in send_message/run_and_broadcast_turn below), so it's the one
    # case where this turn's real USD cost needs computing here instead of
    # being left to deduct_credits.
    cost_usd = Decimal("0")
    if not used_global_key:
        cost_usd = Decimal(str(round(compute_turn_cost_usd(thread.ai_provider, thread.model, usage), 6)))
    # Message creation and the PendingTurn delete below are in the same
    # atomic block, deliberately -- a hard kill (OOM, infra-level restart,
    # not a catchable Python exception) landing between two separate
    # statements here would leave the row alive despite the turn's content
    # already being durably saved, just like the previously-later await
    # this feature already closed one such gap for (see the PendingTurn
    # docstring). Wrapping both in one commit closes it down to "a DB commit
    # is atomic", the practical floor for this without a distributed
    # transaction spanning Celery too.
    with transaction.atomic():
        Message.objects.bulk_create([
            Message(thread=thread, sender="user", content=user_text),
            Message(
                thread=thread, sender="assistant", content=assistant_text, tool_calls=tool_calls or [],
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                estimated_cost_usd=cost_usd,
            ),
        ])
        # pending_turn_id is None for send_message's HTTP path (which never
        # creates a PendingTurn row at all -- no generation_registry claim
        # guards it, so there's nothing of its own to clean up here).
        # Deleting by pk rather than by thread_id matters even on the WS
        # path: a concurrent HTTP send on the same thread must never be able
        # to delete a still-in-flight WS turn's own row out from under it.
        if pending_turn_id is not None:
            PendingTurn.objects.filter(pk=pending_turn_id).delete()
    is_first_turn = not history
    with transaction.atomic():
        locked_thread = Thread.objects.select_for_update().get(pk=thread.pk)
        # Rebuild conversation_state from the Message table (the source of
        # truth, just written above) rather than appending onto the `history`
        # snapshot taken before the — potentially slow — AI call. Two turns on
        # the same thread can run concurrently (e.g. two open tabs); appending
        # onto a stale snapshot means whichever save() lands last silently
        # overwrites the other turn's exchange in conversation_state.
        conversation_state = [
            {"role": m.sender, "content": m.content}
            for m in Message.objects.filter(thread=thread).order_by("timestamp", "id")
        ]
        locked_thread.conversation_state = conversation_state
        update_fields = ["conversation_state", "updated_at"]
        if is_first_turn:
            locked_thread.title = user_text[:100]
            update_fields.append("title")
        locked_thread.save(update_fields=update_fields)
    # Keep the caller's in-memory `thread` object in sync with what was
    # actually persisted (post-rebuild) — callers that hold onto `thread`
    # across multiple turns (e.g. a loop reusing the same instance) expect it
    # to reflect the latest saved state, same as before this rebuild existed.
    thread.conversation_state = conversation_state
    if is_first_turn:
        thread.title = locked_thread.title
        generate_thread_title_task.delay(thread.id, user_text[:500], assistant_text[:500])
    extract_memories_task.delay(thread.user_id, thread.assistant_id, user_text, assistant_text)


def get_usage_summary(user) -> dict:
    """Total token usage across every turn this user has ever generated —
    only assistant rows carry non-zero input_tokens/output_tokens (see
    Message model); filtered explicitly rather than relying on 'user' rows
    always being zero. Sum() returns None over an empty queryset (brand-new
    user, no messages yet), hence the `or 0`."""
    totals = Message.objects.filter(thread__user=user, sender="assistant").aggregate(
        input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens"),
        estimated_cost_usd=Sum("estimated_cost_usd"),
    )
    return {
        "input_tokens": totals["input_tokens"] or 0,
        "output_tokens": totals["output_tokens"] or 0,
        "estimated_cost_usd": float(totals["estimated_cost_usd"] or 0),
    }


async def _deduct_credits_after_persisted_turn(user, thread, usage) -> None:
    """Isolates deduct_credits from the turn's own success/failure handling.
    By the time this runs, the assistant's reply is already saved and the
    user has their answer — a billing failure here must not masquerade as
    (or trigger) a "something went wrong, please try again" chat.error,
    which would be actively misleading and risks a retry that pays for a
    second real provider call while this one goes uncharged either way.
    Logged for ops/billing reconciliation instead of silently swallowed."""
    try:
        await deduct_credits(user, thread.ai_provider, thread.model, usage)
    except Exception:
        logger.exception("Credit deduction failed after a successful, already-saved turn for thread %s", thread.id)


async def send_message(thread, text, user, memories=None) -> str:
    history = thread.conversation_state or []
    tool_calls: list[str] = []

    async def track_tool_call(tool_name):
        tool_calls.append(tool_name)

    response_text, usage, used_global_key = await send_chat_message(
        thread.assistant, text, ai_provider=thread.ai_provider, model=thread.model, user=user,
        conversation_history=history, memories=memories, stream=False, project_id=thread.project_id,
        on_tool_call=track_tool_call,
    )
    await sync_to_async(_record_turn)(thread, history, text, response_text, tool_calls, usage, used_global_key)
    if used_global_key:
        await _deduct_credits_after_persisted_turn(user, thread, usage)
    return response_text


async def run_and_broadcast_turn(thread, text, user, group_name, memories=None):
    """Owns a turn's full lifecycle end to end — unlike the old stream_message
    (a generator the caller drove and could abandon by cancelling), this runs
    to completion on its own regardless of whether any WebSocket connection
    is still attached, broadcasting every frame to `group_name` via the
    Channels group (already Redis-backed, no new infra) instead of calling
    back into a single owning connection. Meant to be scheduled with
    asyncio.create_task and tracked in generation_registry, not awaited
    directly by a request/receive handler — see ConversationConsumer.

    _record_turn/deduct_credits sit inside the try, unconditional on anyone
    being in the group (group_send to an empty group is a documented no-op,
    it never raises) — this is what guarantees the message is always saved
    and credits always deducted exactly once, regardless of connection state
    when the turn finishes. Don't wrap the group_send calls in something that
    would also swallow that.
    """
    from channels.layers import get_channel_layer
    from chat_messages import generation_registry

    channel_layer = get_channel_layer()
    # Durability net for the whole turn, not just the tool-confirmation-wait
    # window (see PendingTurn's docstring) -- written before anything else
    # so even a crash during the very first model call is covered. Cleared
    # in the `finally` below regardless of how the turn ends. Every
    # subsequent read/update/delete of this row is scoped to its own pk,
    # not thread_id -- a concurrent HTTP send on the same thread (see
    # send_message/_record_turn) must never be able to touch a WS turn's
    # own row, and generation_registry.try_claim already guarantees at
    # most one WS turn per thread anyway, so pk is never less precise.
    #
    # Any PendingTurn already on this thread at this exact point cannot be
    # legitimate: try_claim (called by our own caller just before this)
    # already guarantees no other WS turn is concurrently in flight on it,
    # and the HTTP path never creates rows at all -- so a pre-existing one
    # here can only be an orphan from an earlier crash that nobody has
    # reconnected to clean up yet (_join_thread only ever deletes one row
    # per join, by pk). Left alone, it would linger in the DB forever.
    await sync_to_async(PendingTurn.objects.filter(thread_id=thread.id).delete)()
    pending_turn = await sync_to_async(PendingTurn.objects.create)(thread=thread, user_text=text)
    history = thread.conversation_state or []
    tool_calls: list[str] = []

    async def track_tool_call(tool_name):
        tool_calls.append(tool_name)
        # Mirrored onto the PendingTurn row so a reconnecting client's stale
        # row carries this turn's safety signal for ConversationConsumer
        # ._join_thread's auto-replay decision -- on_tool_call fires before
        # a tool's confirmation gate (see AgentTool's docstring), so any
        # entry here, confirmed or not, must disqualify auto-replay. This
        # write is NOT a nice-to-have: unlike the purely informational
        # status broadcast below, a silently-swallowed failure here would
        # leave PendingTurn.tool_calls looking safe (still []) even though
        # this tool is about to run with a real side effect -- exactly the
        # unsafe auto-replay this signal exists to prevent. Let it propagate
        # (aborting the turn, same as any other DB failure mid-turn) rather
        # than degrade silently.
        #
        # CAUTION if you're tempted to asyncio.gather this with the
        # group_send below "since they're independent" (this was actually
        # done, then reverted, in 2026-09 review): they are NOT equally safe
        # to fail. Before bundling any two awaits with return_exceptions=True
        # or similar, ask whether either one is load-bearing for correctness
        # (this write is) rather than just "can these run concurrently".
        await sync_to_async(PendingTurn.objects.filter(pk=pending_turn.pk).update)(tool_calls=tool_calls)
        try:
            await channel_layer.group_send(
                group_name, {"type": "chat.status", "status": "tool_call", "tool": tool_name, "thread_id": thread.id},
            )
        except Exception:
            # Purely informational plumbing -- a client that misses this
            # "using tool X" status frame still gets the tool's actual
            # result normally; not worth aborting an otherwise-succeeding
            # turn over, unlike the PendingTurn write above.
            logger.exception("Failed to broadcast tool_call status for thread %s", thread.id)

    async def track_delegate_start(provider_label):
        # The delegated call (a fresh, one-shot send_chat_message) used to
        # run completely silently — status just sat on "thinking" for the
        # whole round-trip, indistinguishable from a normal short pause.
        await channel_layer.group_send(
            group_name,
            {"type": "chat.status", "status": "delegating", "provider": provider_label, "thread_id": thread.id},
        )

    async def confirm_tool_call(tool_name, arguments, source="built-in"):
        future = asyncio.get_event_loop().create_future()
        # search_project_files feeds attacker-controllable document text
        # into the model's context (see chat_router.py's _get_mcp_context
        # docstring) -- a sensitive tool call proposed later in the same
        # turn may have been shaped by that content, not by anything the
        # user actually asked for. tool_calls already records call order
        # (track_tool_call appends before any executor runs), so this is
        # just a membership check, no new state needed.
        after_file_read = FILE_SEARCH_TOOL["name"] in tool_calls
        # tool/arguments stored alongside the future (not just the future
        # itself) so a client that (re)joins after this broadcast already
        # went out — e.g. reconnecting after the connection that would have
        # seen it dropped — can be sent the same confirm_required prompt
        # again via _join_thread, instead of only a generic "resuming" they
        # have no way to act on.
        generation_registry.set_pending_confirmation(
            thread.id, future, tool_name, arguments, source, after_file_read,
        )
        # No separate DB write here -- the PendingTurn row created at the
        # top of run_and_broadcast_turn already spans this whole window
        # (and the rest of the turn besides), so it covers a crash during
        # confirmation-wait too without a second, narrower durability net.
        # thread_id rides along so a client that doesn't know it yet (a
        # brand-new thread, mid-first-turn, before the 'done' frame ever
        # delivers an id) can still reply with the right thread_id.
        await channel_layer.group_send(
            group_name,
            {
                "type": "chat.confirm_required", "tool": tool_name, "arguments": arguments,
                "source": source, "after_file_read": after_file_read, "thread_id": thread.id,
            },
        )
        try:
            return await asyncio.wait_for(future, timeout=CONFIRMATION_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning("Tool confirmation for thread %s timed out with no client attached", thread.id)
            return False
        finally:
            generation_registry.clear_pending_confirmation(thread.id)

    # Defined before the try, not inside it, so the except asyncio.CancelledError
    # branch below can reference them safely even if cancellation strikes
    # before send_chat_message() itself has returned (i.e. before any of
    # these would otherwise have been assigned).
    collected: list[str] = []
    usage = None
    used_global_key = False

    try:
        chunks, usage, used_global_key = await send_chat_message(
            thread.assistant, text, ai_provider=thread.ai_provider, model=thread.model, user=user,
            conversation_history=history, memories=memories, stream=True, project_id=thread.project_id,
            on_tool_call=track_tool_call, confirm_tool_call=confirm_tool_call, on_delegate_start=track_delegate_start,
        )
        async for chunk in chunks:
            collected.append(chunk)
            generation_registry.append_streamed_chunk(thread.id, chunk)
            await channel_layer.group_send(group_name, {"type": "chat.chunk", "chunk": chunk, "thread_id": thread.id})
        assistant_text = "".join(collected)
        await sync_to_async(_record_turn)(
            thread, history, text, assistant_text, tool_calls, usage, used_global_key,
            pending_turn_id=pending_turn.pk,
        )
        if used_global_key:
            await _deduct_credits_after_persisted_turn(user, thread, usage)
    except InsufficientCreditsError as exc:
        await channel_layer.group_send(group_name, {"type": "chat.error", "error": str(exc), "thread_id": thread.id})
        return
    except asyncio.CancelledError:
        # ConversationConsumer._stop_generation cancelling the registered
        # task — a deliberate user action, not a failure. Whatever was
        # already generated is saved (same treatment as a normal
        # completion) rather than discarded, so the partial response the
        # user was reading doesn't vanish on reload. Deliberately not
        # re-raised: this is a graceful, handled stop, not an unexpected
        # crash, so the task should finish in a normal (not cancelled)
        # state — nothing awaits it anyway (see ConversationConsumer).
        if collected:
            assistant_text = "".join(collected)
            await sync_to_async(_record_turn)(
                thread, history, text, assistant_text, tool_calls, usage, used_global_key,
                pending_turn_id=pending_turn.pk,
            )
            if used_global_key:
                await _deduct_credits_after_persisted_turn(user, thread, usage)
        await channel_layer.group_send(group_name, {"type": "chat.done", "thread_id": thread.id, "stopped": True})
        return
    except Exception:
        logger.exception("Error while streaming chat response for thread %s", thread.id)
        await channel_layer.group_send(
            group_name,
            {
                "type": "chat.error",
                "error": "Something went wrong while generating the response. Please try again.",
                "thread_id": thread.id,
            },
        )
        return
    finally:
        generation_registry.release(thread.id)
        await sync_to_async(PendingTurn.objects.filter(pk=pending_turn.pk).delete)()

    await channel_layer.group_send(group_name, {"type": "chat.done", "thread_id": thread.id})
