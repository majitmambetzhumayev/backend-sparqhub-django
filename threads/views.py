#threads/views.py
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

from .models import Thread
from .serializers import ThreadListSerializer, ThreadDetailSerializer, ThreadUpdateSerializer
from .services import update_thread_provider


@method_decorator(csrf_protect, name='dispatch')
class ThreadListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ThreadListSerializer

    def get_queryset(self):
        qs = Thread.objects.filter(user=self.request.user)
        project_id = self.request.query_params.get('project_id')
        if project_id == 'none':
            qs = qs.filter(project__isnull=True)
        elif project_id:
            qs = qs.filter(project_id=project_id)
        return qs.order_by('-updated_at')


@method_decorator(csrf_protect, name='dispatch')
class ThreadDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Thread.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.request.method in ('PATCH', 'PUT'):
            return ThreadUpdateSerializer
        return ThreadDetailSerializer

    def update(self, request, *args, **kwargs):
        thread = self.get_object()
        serializer = self.get_serializer(thread, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if 'ai_provider' in data or 'model' in data:
            try:
                update_thread_provider(
                    thread,
                    data.get('ai_provider', thread.ai_provider),
                    data.get('model', thread.model),
                )
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if 'title' in data:
            thread.title = data['title']
            thread.save(update_fields=['title', 'updated_at'])

        if 'project' in data:
            thread.project = data['project']
            thread.save(update_fields=['project', 'updated_at'])

        return Response(ThreadDetailSerializer(thread).data)
