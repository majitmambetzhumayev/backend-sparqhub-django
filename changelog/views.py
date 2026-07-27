# changelog/views.py
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny

from .models import ChangelogEntry
from .serializers import ChangelogEntrySerializer


class ChangelogListAPIView(ListAPIView):
    """GET /api/changelog/ -- public, read-only. Backs the landing page's
    patch-notes section; no reason to require auth to read it."""
    queryset = ChangelogEntry.objects.all()
    serializer_class = ChangelogEntrySerializer
    permission_classes = [AllowAny]
