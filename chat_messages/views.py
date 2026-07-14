from asgiref.sync import async_to_sync
from django.views.decorators.csrf import csrf_protect
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from ai_providers.chat_router import InsufficientCreditsError
from chat_messages.models import Message
from chat_messages.serializers import MessageSerializer
from chat_messages.services import send_message
from librarian.services import retrieve_relevant_memories
from projects.models import Project
from threads.models import Thread
from threads.services import get_or_create_thread


@method_decorator(csrf_protect, name='dispatch')
class SendMessageAPIView(APIView):
    """
    POST /api/threads/messages/             → create thread + send first message
    POST /api/threads/<thread_id>/messages/ → append message to existing thread
    GET  /api/threads/<thread_id>/messages/ → list messages for a thread
    """

    def get(self, request, thread_id=None, format=None):
        if thread_id is None:
            return Response({"error": "thread_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            Thread.objects.get(pk=thread_id, user=request.user)
        except Thread.DoesNotExist:
            return Response({"error": "Thread not found."}, status=status.HTTP_404_NOT_FOUND)

        messages = Message.objects.filter(thread_id=thread_id, thread__user=request.user).order_by('timestamp')
        return Response(MessageSerializer(messages, many=True).data)

    def post(self, request, thread_id=None, format=None):
        text = request.data.get("message")
        if not text:
            return Response({"error": "'message' is required."}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user

        try:
            thread = get_or_create_thread(
                user,
                thread_id=thread_id,
                ai_provider=request.data.get("ai_provider"),
                model=request.data.get("model"),
                project_id=request.data.get("project_id"),
            )
        except Thread.DoesNotExist:
            return Response({"error": "Thread not found."}, status=status.HTTP_404_NOT_FOUND)
        except Project.DoesNotExist:
            return Response({"error": "Project not found."}, status=status.HTTP_404_NOT_FOUND)

        memories = retrieve_relevant_memories(user, text)
        try:
            response_text = async_to_sync(send_message)(thread, text, user, memories=memories)
        except InsufficientCreditsError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_402_PAYMENT_REQUIRED)

        return Response({"response": response_text, "thread": thread.id}, status=status.HTTP_200_OK)
