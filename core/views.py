from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from assistants.models import Assistant
from assistants.serializers import AssistantSerializer

class QuickChatDataAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        user = request.user
        # Get assistants for the current user ordered by last_used_at descending
        assistants = Assistant.objects.filter(user=user).order_by('-last_used_at')
        assistants_data = AssistantSerializer(assistants, many=True).data
        
        # Define default assistant as the most recently used one
        default_assistant = assistants.first() if assistants.exists() else None
        default_thread = None
        
        # If a default assistant exists, try to pick its most recent thread
        if default_assistant:
            threads = default_assistant.threads.order_by('-updated_at')
            default_thread = threads.first() if threads.exists() else None

        data = {
            "assistants": assistants_data,
            "default_assistant": default_assistant.id if default_assistant else None,
            "default_thread": default_thread.id if default_thread else None,
        }
        return Response(data)
