# chat_messages/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from ai_providers.chat_router import send_chat_message  # must support async streaming
from threads.models    import Thread
from chat_messages.models import Message

class QuickChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # Accept connection
        await self.accept()

    async def receive(self, text_data):
        """
        Expects JSON: {"assistant_id": int, "thread_id": int|null, "message": str}
        Streams back each token as: {"chunk": "..."}
        """
        data = json.loads(text_data)
        assistant_id = data.get("assistant_id")
        thread_id    = data.get("thread_id")
        message_text = data.get("message")

        if not assistant_id or not message_text:
            await self.send(json.dumps({"error":"Missing fields"}))
            return

        # 1) Fetch/create thread synchronously
        if thread_id is None:
            thread = await sync_to_async(Thread.objects.create)(
                user=self.scope["user"], assistant_id=assistant_id
            )
        else:
            thread = await sync_to_async(Thread.objects.get)(
                pk=thread_id, user=self.scope["user"]
            )

        # 2) Save user message
        await sync_to_async(Message.objects.create)(
            thread=thread, sender="user", content=message_text
        )

        # 3) Stream AI response
        async for chunk in send_chat_message(
            thread.assistant, message_text, stream=True
        ):
            # Send each token/chunk immediately
            await self.send(json.dumps({"chunk": chunk}))

        # 4) Save the full assistant reply
        full_reply = ""  # accumulate if desired
        # (you might collect chunks into full_reply here)
        await sync_to_async(Message.objects.create)(
            thread=thread, sender="assistant", content=full_reply
        )
