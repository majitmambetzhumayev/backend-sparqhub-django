from asgiref.sync import async_to_sync
from django.views.decorators.csrf import csrf_protect
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ai_providers.chat_router import send_chat_message
from chat_messages.models import Message
from librarian.services import retrieve_relevant_memories
from threads.models import Thread


@method_decorator(csrf_protect, name=’dispatch’)
class SendMessageAPIView(APIView):
    """
    POST /api/threads/             → create thread + send first message
    POST /api/threads/<thread_id>/ → append message to existing thread
    """

    def post(self, request, thread_id=None, format=None):
        text = request.data.get("message")
        assistant_id = request.data.get("assistant_id")
        if not text or not assistant_id:
            return Response(
                {"error": "Both ‘message’ and ‘assistant_id’ are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user

        if thread_id is not None:
            try:
                thread = Thread.objects.select_related(‘assistant’).get(pk=thread_id, user=user)
            except Thread.DoesNotExist:
                return Response({"error": "Thread not found."}, status=status.HTTP_404_NOT_FOUND)
        else:
            thread = Thread.objects.create(user=user, assistant_id=assistant_id, conversation_state=[])

        memories = retrieve_relevant_memories(user, text)
        response_text = async_to_sync(send_chat_message)(thread.assistant, text, memories=memories)

        Message.objects.bulk_create([
            Message(thread=thread, sender="user", content=text),
            Message(thread=thread, sender="assistant", content=response_text),
        ])

        return Response({"response": response_text, "thread": thread.id}, status=status.HTTP_200_OK)
