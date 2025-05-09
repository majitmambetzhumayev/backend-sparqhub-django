#assistants/views.py
import logging
from rest_framework import viewsets, status
from rest_framework.response import Response
from users.authentication import CookieJWTAuthentication
from rest_framework.permissions import IsAuthenticated
from .models import Assistant
from .serializers import AssistantSerializer
from ai_providers.factory import get_provider  # Returns the provider integration instance
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

logger = logging.getLogger(__name__)

class AssistantViewSet(viewsets.ModelViewSet):
    """
    CRUD for assistants.
    - For remote-persistent providers, creation and update must sync with the provider.
    - Soft delete is implemented locally (and optionally, remote deletion if supported).
    """
    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [IsAuthenticated]
    # permission_classes = [AllowAny]
    serializer_class = AssistantSerializer
    

    def get_queryset(self):
        return Assistant.objects.filter(user=self.request.user, deleted=False)
    
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        # Enforce remote sync for providers that require persistence
        ai_provider = request.data.get("ai_provider", "openai")
        provider = get_provider(ai_provider)
        # For remote-persistent providers, we enforce remote creation.
        # If the provider supports CRUD, then create remotely.
        if provider.supports_crud:
            try:
                assistant = provider.create_assistant(
                    user=request.user,
                    name=request.data["name"],
                    instructions=request.data.get("instructions", ""),
                    model=request.data.get("model", "gpt-4o")
                )
                serializer = self.get_serializer(assistant)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.exception("Remote creation failed: %s", e)
                return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        else:
            # Otherwise, create locally (for non-persistent providers)
            return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        assistant = self.get_object()
        provider = get_provider(assistant.ai_provider)
        if provider.supports_crud:
            try:
                assistant = provider.update_assistant(
                    assistant_id=assistant.provider_assistant_id,
                    user=request.user,
                    name=request.data.get("name"),
                    instructions=request.data.get("instructions"),
                    model=request.data.get("model")
                )
                serializer = self.get_serializer(assistant)
                return Response(serializer.data, status=status.HTTP_200_OK)
            except Exception as e:
                logger.exception("Remote update failed: %s", e)
                return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        else:
            return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        assistant = self.get_object()
        provider = get_provider(assistant.ai_provider)
        # Soft delete locally; if provider supports remote deletion, attempt that too.
        if provider.supports_crud:
            try:
                provider.delete_assistant(assistant.provider_assistant_id, request.user)
            except Exception as e:
                logger.warning("Remote delete failed (proceeding with local soft delete): %s", e)
        assistant.deleted = True
        assistant.save()
        return Response(status=status.HTTP_204_NO_CONTENT)
