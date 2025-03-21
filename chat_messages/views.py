from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import get_user_model
from chat_messages.serializers import MessageSerializer
from ai_providers.chat_router import send_chat_message

User = get_user_model()

class SendMessageAPIView(APIView):
    def post(self, request, thread_id, format=None):
        message_text = request.data.get("message")
        if not message_text:
            return Response({"error": "Message text is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            # Assume we have a way to fetch the assistant from the thread or from the request
            assistant = ...  # retrieve your Assistant instance accordingly
            response = send_chat_message(assistant, message_text, stream=False)
            return Response({"response": response}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
