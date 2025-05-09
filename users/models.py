# users/models.py

from django.contrib.auth.models import AbstractUser, Group, Permission
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField

class CustomUser(AbstractUser):
    """
    CustomUser extends AbstractUser and overrides the
    built-in many‐to‐many fields to avoid reverse accessor collisions.
    """
    bio = models.TextField(blank=True, null=True)
    profile_picture = models.URLField(blank=True, null=True)
    timezone = models.CharField(max_length=50, default="UTC")
    preferred_integration = models.CharField(
        max_length=20,
        choices=(
            ('openai', 'OpenAI'),
            ('mistral', 'Mistral'),
            ('made', 'Made.com'),
        ),
        default='openai',
    )
    phone_number = models.CharField(max_length=20, blank=True, null=True)

    # Override the built-in m2m fields:
    groups = models.ManyToManyField(
        Group,
        related_name="customuser_set",  # avoid 'user_set' clash
        blank=True,
        help_text="The groups this user belongs to."
    )
    user_permissions = models.ManyToManyField(
        Permission,
        related_name="customuser_set",  # avoid 'user_set' clash
        blank=True,
        help_text="Specific permissions for this user."
    )

    def __str__(self):
        return self.username
