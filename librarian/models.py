from django.db import models
from django.conf import settings
from pgvector.django import VectorField


class MemoryEntry(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memories',
    )
    content = models.TextField()
    # 384-dim matches all-MiniLM-L6-v2
    embedding = VectorField(dimensions=384)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user_id} — {self.content[:60]}"
