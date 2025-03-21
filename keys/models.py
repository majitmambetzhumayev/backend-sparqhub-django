from django.db import models
from django.conf import settings
from encrypted_model_fields.fields import EncryptedCharField

KEY_TYPE_CHOICES = (
    ('openai', 'OpenAI'),
    ('mistral', 'Mistral'),
    ('made', 'Made.com'),
    # Add other integration types as needed
)

class APIKey(models.Model):
    # Optionally associate the key with a user (for personal keys) or leave it null for shared/team keys.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='api_keys',
        null=True,
        blank=True
    )
    key_type = models.CharField(max_length=20, choices=KEY_TYPE_CHOICES)
    encrypted_key = EncryptedCharField(max_length=255)
    encryption_key_id = models.CharField(max_length=50)  # Used to track key versions/rotations
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_key_type_display()} API Key (ID: {self.encryption_key_id})"
