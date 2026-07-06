# chat_messages/consumers.py
import asyncio
import json
import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from ai_providers.chat_router import InsufficientCreditsError
from chat_messages.services import stream_message
from librarian.services import retrieve_relevant_memories
from threads.models import Thread
from threads.services import get_or_create_thread

logger = logging.getLogger(__name__)


class ConversationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        if self.scope["user"].is_anonymous:
            await self.close(code=4001)
            return
        self._pending_confirmation: asyncio.Future | None = None
        self._active_task: asyncio.Task | None = None
        await self.accept()

    async def disconnect(self, close_code):
        if self._pending_confirmation is not None and not self._pending_confirmation.done():
            self._pending_confirmation.cancel()
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()

    async def receive(self, text_data):
        """
        Expects JSON: {"thread_id": int|null, "message": str, "ai_provider"?: str, "model"?: str, "project_id"?: int}
        `ai_provider`/`model`/`project_id` are only consulted when `thread_id` is null (creation
        time) — an existing thread's provider/model is read fresh from the DB on every send, so a
        PATCH to /api/threads/<id>/ takes effect on the next message with no extra wiring.
        Streams back {"chunk": "..."} frames, then a final {"done": true, "thread_id": int}.

        A turn can pause mid-stream waiting on user approval for a sensitive tool
        (e.g. delegate_to_model): {"status": "confirm_required", "tool": ..., "arguments": ...}
        is sent, and the client replies on the same connection with
        {"type": "tool_confirmation", "confirmed": bool}. Since Channels dispatches
        messages for one connection sequentially, the turn itself runs as a
        background task so this method can return and let the confirmation frame
        be received while the turn is paused awaiting it.
        """
        data = json.loads(text_data)

        if data.get("type") == "tool_confirmation":
            if self._pending_confirmation is not None and not self._pending_confirmation.done():
                self._pending_confirmation.set_result(bool(data.get("confirmed")))
            return

        # One turn at a time per connection: _pending_confirmation/_active_task
        # are single instance attributes, so a second concurrent turn would
        # silently steal/overwrite the first turn's confirmation future,
        # leaving it hanging forever with no error. Only the client-side
        # "busy" state prevented this before — nothing stopped a raw/buggy
        # client from sending a second message while a turn is in flight.
        if self._active_task is not None and not self._active_task.done():
            await self.send(json.dumps({"error": "A previous message is still being processed."}))
            return

        self._active_task = asyncio.create_task(self._handle_chat_message(data))

    async def _confirm_tool_call(self, tool_name: str, arguments: dict) -> bool:
        self._pending_confirmation = asyncio.get_event_loop().create_future()
        await self.send(json.dumps({"status": "confirm_required", "tool": tool_name, "arguments": arguments}))
        try:
            return await self._pending_confirmation
        finally:
            self._pending_confirmation = None

    async def _handle_chat_message(self, data):
        thread_id = data.get("thread_id")
        message_text = data.get("message")
        ai_provider = data.get("ai_provider")
        model = data.get("model")
        project_id = data.get("project_id")

        if not message_text:
            await self.send(json.dumps({"error": "Missing fields"}))
            return

        user = self.scope["user"]
        try:
            thread = await sync_to_async(get_or_create_thread)(
                user, thread_id=thread_id, ai_provider=ai_provider, model=model, project_id=project_id,
            )
        except Thread.DoesNotExist:
            await self.send(json.dumps({"error": "Thread not found."}))
            return

        memories = await sync_to_async(retrieve_relevant_memories)(user, message_text)

        async def on_tool_call(tool_name):
            await self.send(json.dumps({"status": "tool_call", "tool": tool_name}))

        await self.send(json.dumps({"status": "thinking"}))
        try:
            async for chunk in stream_message(
                thread, message_text, user, memories=memories,
                on_tool_call=on_tool_call, confirm_tool_call=self._confirm_tool_call,
            ):
                await self.send(json.dumps({"chunk": chunk}))
        except InsufficientCreditsError as exc:
            await self.send(json.dumps({"error": str(exc)}))
            return
        except Exception:
            logger.exception("Error while streaming chat response for thread %s", thread.id)
            await self.send(json.dumps({"error": "Something went wrong while generating the response. Please try again."}))
            return

        await self.send(json.dumps({"done": True, "thread_id": thread.id}))
