#threads/models.py
from django.db import models
from django.conf import settings
from assistants.models import Assistant

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
    # Stores conversation history as a list of messages (dictionaries)
    conversation_state = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Thread {self.id} for {self.assistant.name}"
