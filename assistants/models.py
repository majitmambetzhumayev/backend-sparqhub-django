#assistants/models.py
from django.db import models
from django.conf import settings

AI_PROVIDER_CHOICES = (
    ('anthropic', 'Anthropic'),
    ('openai', 'OpenAI'),
    ('mistral', 'Mistral'),
    ('gemini', 'Gemini'),
)

class Assistant(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='assistants'
    )
    provider_assistant_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    name = models.CharField(max_length=255)
    instructions = models.TextField(blank=True)
    model = models.CharField(max_length=100, default='claude-sonnet-4-6')
    metadata = models.JSONField(blank=True, null=True)
    deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)
    ai_provider = models.CharField(max_length=50, choices=AI_PROVIDER_CHOICES, default='anthropic')
    is_persistent = models.BooleanField(default=False)
    supports_crud = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} ({self.ai_provider})"
