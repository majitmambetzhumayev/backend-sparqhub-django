# users/models.py
from django.contrib.auth.models import AbstractUser
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField  # Optional: if you want to store a key here

class CustomUser(AbstractUser):
    """
    Custom user model that extends Django's AbstractUser.
    This model includes additional fields for user profiles and integration preferences,
    making it future‑proof for features like API key management and team collaboration.
    """

    # Optional bio or description field
    bio = models.TextField(blank=True, null=True, help_text="A short biography of the user.")

    # Profile picture URL (could be a FileField/ImageField if you handle uploads)
    profile_picture = models.URLField(blank=True, null=True, help_text="URL to the user's profile picture.")

    # Timezone for scheduling and localization
    timezone = models.CharField(max_length=50, default="UTC", help_text="User's preferred timezone.")

    # Preferred integration type; useful if users have multiple options (e.g., openai, mistral, etc.)
    preferred_integration = models.CharField(
        max_length=20,
        choices=(
            ('openai', 'OpenAI'),
            ('mistral', 'Mistral'),
            ('made', 'Made.com'),
        ),
        default='openai',
        help_text="Preferred API integration for the user."
    )

    # Optional: Personal API key stored securely.
    # If you plan to manage keys centrally in a separate app, you might omit this field.
    # openai_api_key = EncryptedCharField(max_length=255, blank=True, null=True, help_text="User's personal OpenAI API key.")

    # Optional: Phone number for two-factor authentication or notifications.
    phone_number = models.CharField(max_length=20, blank=True, null=True, help_text="User's phone number for notifications or 2FA.")

    def __str__(self):
        return self.username
