#assistants/views.py
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.views import APIView
from users.authentication import CookieJWTAuthentication
from rest_framework.permissions import IsAuthenticated
from ai_providers.factory import PROVIDERS
from .models import Assistant
from .serializers import AssistantSerializer


class AssistantViewSet(viewsets.ModelViewSet):
    """CRUD for assistants. Purely local persistence — provider selection only matters at chat time."""
    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = AssistantSerializer

    def get_queryset(self):
        return Assistant.objects.filter(user=self.request.user, deleted=False, is_persistent=False)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        assistant = self.get_object()
        assistant.deleted = True
        assistant.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AvailableProvidersAPIView(APIView):
    """Which providers/models a user can pick when creating an assistant — derived from the factory registry."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            name: {"label": cls.label, "models": cls.AVAILABLE_MODELS}
            for name, cls in PROVIDERS.items()
        })
