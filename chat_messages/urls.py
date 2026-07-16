#chat_messages/urls.py
from django.urls import path
from .views import SendMessageAPIView, UsageSummaryAPIView

urlpatterns = [
    # new thread
    path('threads/messages/', SendMessageAPIView.as_view(), name='message-list-create-thread'),
    # existing thread
    path('threads/<int:thread_id>/messages/', SendMessageAPIView.as_view(), name='message-list'),
    path('usage/summary/', UsageSummaryAPIView.as_view(), name='usage-summary'),
]
