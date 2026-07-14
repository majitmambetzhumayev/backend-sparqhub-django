# keys/views.py
from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .models import APIKey
from .serializers import APIKeySerializer, APIKeyWriteSerializer
from .services import create_or_update_user_api_key

class APIKeyViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """No update/partial_update — deliberately not a full ModelViewSet.
    Rotating a key's secret is done by POSTing the same key_type again
    (create_or_update_user_api_key upserts on (user, key_type)); there is
    no legitimate PUT/PATCH use case, and allowing one let key_type be
    relabeled on an existing row without ever touching the actual secret,
    silently mislabeling which provider a credential belongs to."""
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return APIKey.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.action == 'create':
            return APIKeyWriteSerializer
        return APIKeySerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = create_or_update_user_api_key(
            request.user,
            serializer.validated_data['key_type'],
            serializer.validated_data['raw_key'],
        )
        return Response(APIKeySerializer(key).data, status=status.HTTP_201_CREATED)
