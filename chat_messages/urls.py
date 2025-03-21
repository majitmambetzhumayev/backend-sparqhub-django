from django.urls import path
from .views import SendMessageAPIView

urlpatterns = [
    path('threads/<int:thread_id>/messages/', SendMessageAPIView.as_view(), name='message-list'),
]
