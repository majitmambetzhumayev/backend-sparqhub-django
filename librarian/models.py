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
    # 1024-dim matches Mistral's mistral-embed (doesn't support output_dimension
    # truncation — confirmed via a live API call, its docs are ambiguous on this)
    embedding = VectorField(dimensions=1024)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user_id} — {self.content[:60]}"
