#projects/views.py
from django.db.models import Count
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from .models import Project
from .serializers import ProjectSerializer


class ProjectViewSet(viewsets.ModelViewSet):
    """CRUD for projects. Conversations attach via Thread.project (SET_NULL on delete)."""
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectSerializer

    def get_queryset(self):
        return Project.objects.filter(user=self.request.user).annotate(thread_count=Count('threads'))

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
