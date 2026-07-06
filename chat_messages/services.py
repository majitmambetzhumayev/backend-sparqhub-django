from asgiref.sync import sync_to_async

from ai_providers.chat_router import send_chat_message, deduct_credits
from chat_messages.models import Message
from librarian.tasks import extract_memories_task
from threads.tasks import generate_thread_title_task


def _record_turn(thread, history, user_text, assistant_text):
    Message.objects.bulk_create([
        Message(thread=thread, sender="user", content=user_text),
        Message(thread=thread, sender="assistant", content=assistant_text),
    ])
    thread.conversation_state = [
        *history,
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    ]
    update_fields = ["conversation_state", "updated_at"]
    if not history:
        thread.title = user_text[:100]
        update_fields.append("title")
        generate_thread_title_task.delay(thread.id, user_text[:500], assistant_text[:500])
    thread.save(update_fields=update_fields)
    extract_memories_task.delay(thread.user_id, thread.assistant_id, user_text, assistant_text)


async def send_message(thread, text, user, memories=None) -> str:
    history = thread.conversation_state or []
    response_text, usage, used_global_key = await send_chat_message(
        thread.assistant, text, ai_provider=thread.ai_provider, model=thread.model, user=user,
        conversation_history=history, memories=memories, stream=False, project_id=thread.project_id,
    )
    await sync_to_async(_record_turn)(thread, history, text, response_text)
    if used_global_key:
        await deduct_credits(user, thread.ai_provider, thread.model, usage)
    return response_text


async def stream_message(thread, text, user, memories=None, on_tool_call=None, confirm_tool_call=None):
    history = thread.conversation_state or []
    chunks, usage, used_global_key = await send_chat_message(
        thread.assistant, text, ai_provider=thread.ai_provider, model=thread.model, user=user,
        conversation_history=history, memories=memories, stream=True, project_id=thread.project_id,
        on_tool_call=on_tool_call, confirm_tool_call=confirm_tool_call,
    )
    collected = []
    async for chunk in chunks:
        collected.append(chunk)
        yield chunk
    await sync_to_async(_record_turn)(thread, history, text, "".join(collected))
    if used_global_key:
        await deduct_credits(user, thread.ai_provider, thread.model, usage)
