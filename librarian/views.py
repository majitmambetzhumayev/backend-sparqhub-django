from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.views.decorators.csrf import csrf_protect
from django.utils.decorators import method_decorator

from librarian.serializers import MemoryEntrySerializer
from librarian.services import store_memory


@method_decorator(csrf_protect, name='dispatch')
class MemoryEntryCreateView(APIView):
    """POST /api/memories/ — store a new memory for the authenticated user."""

    def post(self, request):
        serializer = MemoryEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = store_memory(request.user, serializer.validated_data['content'])
        return Response(MemoryEntrySerializer(entry).data, status=status.HTTP_201_CREATED)
