# users/models.py

from django.contrib.auth.models import AbstractUser, Group, Permission, UserManager
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


class CustomUserManager(UserManager):
    @classmethod
    def normalize_email(cls, email):
        # The base implementation coerces a falsy email to '' — but multiple
        # '' values collide under email's unique constraint below, while
        # multiple NULLs don't. Omitting email (existing accounts, test
        # fixtures, admin-created users) should end up NULL, not ''.
        if not email:
            return None
        return super().normalize_email(email)


class CustomUser(AbstractUser):
    """
    CustomUser extends AbstractUser and overrides the
    built-in many‐to‐many fields to avoid reverse accessor collisions.
    """
    # Overrides AbstractUser's email (blank=True, no uniqueness). null=True
    # (not just blank='') so existing/omitted-email accounts don't collide
    # under the unique constraint — Postgres treats multiple '' as a
    # violation but allows multiple NULLs. default=None (not the usual
    # empty-string default for CharField-based fields) so a user created
    # without one actually gets NULL, not '' — otherwise every such user
    # would still collide with each other despite null=True. New
    # registrations always require a real one (enforced at the serializer
    # level, not here).
    email = models.EmailField(unique=True, null=True, blank=True, default=None)

    objects = CustomUserManager()
    bio = models.TextField(blank=True, null=True)
    profile_picture = models.URLField(blank=True, null=True)
    timezone = models.CharField(max_length=50, default="UTC")
    preferred_integration = models.CharField(
        max_length=20,
        choices=(
            ('anthropic', 'Anthropic'),
        ),
        default='anthropic',
    )
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    credits_remaining = models.IntegerField(default=100)

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
