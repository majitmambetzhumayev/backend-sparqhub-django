from django.db import models
from django.conf import settings

AI_PROVIDER_CHOICES = (
    ('openai', 'OpenAI'),
    ('mistral', 'Mistral'),
    ('claude', 'Claude'),
    ('gemini', 'Gemini'),
)

class Assistant(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='assistants'
    )
    # Generalized field for storing the provider's assistant ID (if available)
    provider_assistant_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    name = models.CharField(max_length=255)
    instructions = models.TextField(blank=True)
    model = models.CharField(max_length=100)
    metadata = models.JSONField(blank=True, null=True)
    # Soft deletion flag; true means "deleted" locally.
    deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)
    ai_provider = models.CharField(max_length=50, choices=AI_PROVIDER_CHOICES, default='openai')
    # Indicates if the assistant has been synced/created remotely
    is_persistent = models.BooleanField(default=False)
    # Indicates if the provider supports remote CRUD operations for this assistant
    supports_crud = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} ({self.ai_provider})"
