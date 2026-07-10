import asyncio
import logging

from asgiref.sync import sync_to_async
from django.db import transaction

from ai_providers.chat_router import InsufficientCreditsError, send_chat_message, deduct_credits
from chat_messages.models import Message
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


def _record_turn(thread, history, user_text, assistant_text, tool_calls=None):
    Message.objects.bulk_create([
        Message(thread=thread, sender="user", content=user_text),
        Message(thread=thread, sender="assistant", content=assistant_text, tool_calls=tool_calls or []),
    ])
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
    await sync_to_async(_record_turn)(thread, history, text, response_text, tool_calls)
    if used_global_key:
        await deduct_credits(user, thread.ai_provider, thread.model, usage)
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
    history = thread.conversation_state or []
    tool_calls: list[str] = []

    async def track_tool_call(tool_name):
        tool_calls.append(tool_name)
        await channel_layer.group_send(group_name, {"type": "chat.status", "status": "tool_call", "tool": tool_name})

    async def confirm_tool_call(tool_name, arguments):
        future = asyncio.get_event_loop().create_future()
        # tool/arguments stored alongside the future (not just the future
        # itself) so a client that (re)joins after this broadcast already
        # went out — e.g. reconnecting after the connection that would have
        # seen it dropped — can be sent the same confirm_required prompt
        # again via _join_thread, instead of only a generic "resuming" they
        # have no way to act on.
        generation_registry.set_pending_confirmation(thread.id, future, tool_name, arguments)
        # thread_id rides along so a client that doesn't know it yet (a
        # brand-new thread, mid-first-turn, before the 'done' frame ever
        # delivers an id) can still reply with the right thread_id.
        await channel_layer.group_send(
            group_name,
            {"type": "chat.confirm_required", "tool": tool_name, "arguments": arguments, "thread_id": thread.id},
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
            on_tool_call=track_tool_call, confirm_tool_call=confirm_tool_call,
        )
        async for chunk in chunks:
            collected.append(chunk)
            await channel_layer.group_send(group_name, {"type": "chat.chunk", "chunk": chunk})
        assistant_text = "".join(collected)
        await sync_to_async(_record_turn)(thread, history, text, assistant_text, tool_calls)
        if used_global_key:
            await deduct_credits(user, thread.ai_provider, thread.model, usage)
    except InsufficientCreditsError as exc:
        await channel_layer.group_send(group_name, {"type": "chat.error", "error": str(exc)})
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
            await sync_to_async(_record_turn)(thread, history, text, assistant_text, tool_calls)
            if used_global_key:
                await deduct_credits(user, thread.ai_provider, thread.model, usage)
        await channel_layer.group_send(group_name, {"type": "chat.done", "thread_id": thread.id, "stopped": True})
        return
    except Exception:
        logger.exception("Error while streaming chat response for thread %s", thread.id)
        await channel_layer.group_send(
            group_name,
            {"type": "chat.error", "error": "Something went wrong while generating the response. Please try again."},
        )
        return
    finally:
        generation_registry.release(thread.id)

    await channel_layer.group_send(group_name, {"type": "chat.done", "thread_id": thread.id})
