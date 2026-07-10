# project_files/views.py
from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated

from .models import ProjectFile
from .serializers import ProjectFileSerializer
from .services import delete_project_file


class ProjectFileViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Upload/list/delete a project's files — no update: files are
    immutable once uploaded, so this deliberately isn't a full ModelViewSet
    (no PUT/PATCH)."""
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectFileSerializer

    def get_queryset(self):
        qs = ProjectFile.objects.filter(project__user=self.request.user).select_related('project')
        project_id = self.request.query_params.get('project_id')
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs

    def perform_destroy(self, instance):
        delete_project_file(instance)
