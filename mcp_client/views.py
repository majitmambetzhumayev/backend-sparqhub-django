# mcp_client/views.py
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from .models import MCPServer
from .serializers import MCPServerSerializer


class MCPServerViewSet(viewsets.ModelViewSet):
    """CRUD for a project's MCP tool servers."""
    permission_classes = [IsAuthenticated]
    serializer_class = MCPServerSerializer

    def get_queryset(self):
        qs = MCPServer.objects.filter(project__user=self.request.user).select_related('project')
        project_id = self.request.query_params.get('project_id')
        if project_id:
            qs = qs.filter(project_id=project_id)
        return qs.order_by('name')
