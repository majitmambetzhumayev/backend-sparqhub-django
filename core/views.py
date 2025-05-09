#core/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from assistants.models import Assistant
from assistants.serializers import AssistantSerializer
from .serializers import QuickChatMetadataSerializer
from django.views.decorators.csrf import ensure_csrf_cookie

from rest_framework.permissions import AllowAny
from django.utils.decorators import method_decorator

class QuickChatDataAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        user = request.user
        # 1) All assistants the user can chat with, ordered by last_used_at
        assistants = Assistant.objects.filter(user=user).order_by('-last_used_at')
        assistants_data = AssistantSerializer(assistants, many=True).data

        # 2) Pick the “default” assistant & thread if any exist
        default_assistant = assistants.first() if assistants.exists() else None
        default_thread = None
        if default_assistant:
            threads = default_assistant.threads.order_by('-updated_at')
            default_thread = threads.first() if threads.exists() else None

        payload = {
            'assistants':        assistants_data,
            'default_assistant': default_assistant.id  if default_assistant else None,
            'default_thread':    default_thread.id     if default_thread    else None,
        }
        return Response(QuickChatMetadataSerializer(payload).data)



@method_decorator(ensure_csrf_cookie, name='dispatch')
class CsrfTokenView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return Response({'detail': 'CSRF cookie set'})