# keys/services.py
from uuid import uuid4

from .models import APIKey


async def get_user_api_key(user, key_type):
    """Retrieve the most recently created API key for a given user and key_type."""
    return await APIKey.objects.filter(user=user, key_type=key_type).order_by('-created_at').afirst()


def create_or_update_user_api_key(user, key_type, raw_key):
    """
    Create or update an API key for the given user and key_type.
    The 'raw_key' is transparently encrypted by EncryptedCharField.
    """
    key_obj, created = APIKey.objects.update_or_create(
        user=user,
        key_type=key_type,
        defaults={
            'encrypted_key': raw_key,
            'encryption_key_id': uuid4().hex,
        }
    )
    return key_obj


def delete_user_api_key(user, key_type):
    """Delete an API key for the given user and key_type."""
    qs = APIKey.objects.filter(user=user, key_type=key_type)
    deleted_count, _ = qs.delete()
    return deleted_count
