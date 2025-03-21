# keys/views.py
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import APIKey
from .serializers import APIKeySerializer

class APIKeyViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = APIKeySerializer

    def get_queryset(self):
        return APIKey.objects.filter(user=self.request.user)
    
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
