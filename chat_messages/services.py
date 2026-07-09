from asgiref.sync import sync_to_async
from django.db import transaction

from ai_providers.chat_router import send_chat_message, deduct_credits
from chat_messages.models import Message
from librarian.tasks import extract_memories_task
from threads.models import Thread
from threads.tasks import generate_thread_title_task


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


async def stream_message(thread, text, user, memories=None, on_tool_call=None, confirm_tool_call=None):
    history = thread.conversation_state or []
    tool_calls: list[str] = []

    async def track_tool_call(tool_name):
        tool_calls.append(tool_name)
        if on_tool_call is not None:
            await on_tool_call(tool_name)

    chunks, usage, used_global_key = await send_chat_message(
        thread.assistant, text, ai_provider=thread.ai_provider, model=thread.model, user=user,
        conversation_history=history, memories=memories, stream=True, project_id=thread.project_id,
        on_tool_call=track_tool_call, confirm_tool_call=confirm_tool_call,
    )
    collected = []
    async for chunk in chunks:
        collected.append(chunk)
        yield chunk
    await sync_to_async(_record_turn)(thread, history, text, "".join(collected), tool_calls)
    if used_global_key:
        await deduct_credits(user, thread.ai_provider, thread.model, usage)
