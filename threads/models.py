#threads/models.py
from django.db import models
from django.conf import settings
from assistants.models import Assistant, AI_PROVIDER_CHOICES

class Thread(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='threads'
    )
    assistant = models.ForeignKey(
        Assistant,
        on_delete=models.CASCADE,
        related_name='threads'
    )
    ai_provider = models.CharField(max_length=50, choices=AI_PROVIDER_CHOICES, default='anthropic')
    model = models.CharField(max_length=100, default='claude-sonnet-5')
    title = models.CharField(max_length=100, blank=True, default="")
    project = models.ForeignKey(
        'projects.Project',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='threads',
    )
    # Stores conversation history as a list of messages (dictionaries)
    conversation_state = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Thread {self.id} for {self.assistant.name}"
