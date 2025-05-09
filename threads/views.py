#threads/views.py
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from .models import Thread
from .serializers import ThreadSerializer
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

@method_decorator(csrf_protect, name='dispatch')
class ThreadListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class   = ThreadSerializer

    def get_queryset(self):
        user = self.request.user
        assistant_id = self.request.query_params.get('assistant_id')
        qs = Thread.objects.filter(user=user)
        if assistant_id:
            qs = qs.filter(assistant_id=assistant_id)
        return qs.order_by('-created_at')
