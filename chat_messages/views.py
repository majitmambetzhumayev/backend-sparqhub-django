# chat_messages/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import get_user_model
from django.views.decorators.csrf import csrf_protect
from django.utils.decorators import method_decorator
from threads.models import Thread
from chat_messages.models import Message
from ai_providers.chat_router import send_chat_message
from asgiref.sync import async_to_sync

User = get_user_model()

@method_decorator(csrf_protect, name='dispatch')
class SendMessageAPIView(APIView):
    """
    POST /api/threads/               → create new thread + messages
    POST /api/threads/<thread_id>/   → append to existing thread
    """

    def post(self, request, thread_id=None, format=None):
        # 1) Validate inputs
        text = request.data.get("message")
        assistant_id = request.data.get("assistant_id")
        if not text or not assistant_id:
            return Response(
                {"error": "Both 'message' and 'assistant_id' are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2) Authenticated user (sync, in DRF’s threadpool)
        user = request.user
        if not user or not user.is_authenticated:
            return Response({"error": "Authentication required."}, status=status.HTTP_401_UNAUTHORIZED)

        # 3) Load or create Thread
        if thread_id is not None:
            try:
                thread = Thread.objects.get(pk=thread_id, user=user)
            except Thread.DoesNotExist:
                return Response({"error": "Thread not found."}, status=status.HTTP_404_NOT_FOUND)
        else:
            thread = Thread.objects.create(
                user=user,
                assistant_id=assistant_id,
                conversation_state=[]
            )

        # 4) Call AI router (sync or async) via async_to_sync
        #    This will run async functions in a safe sync context.
        response_text = async_to_sync(send_chat_message)(
            thread.assistant,
            text,
            stream=False
        )

        # 5) Persist both user message and assistant reply
        Message.objects.create(thread=thread, sender="user", content=text)
        Message.objects.create(thread=thread, sender="assistant", content=response_text)

        # 6) Return
        return Response(
            {"response": response_text, "thread": thread.id},
            status=status.HTTP_200_OK
        )
