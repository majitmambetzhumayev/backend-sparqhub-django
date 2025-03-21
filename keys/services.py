# keys/services.py

from django.core.exceptions import ObjectDoesNotExist
from .models import APIKey

def get_user_api_key(user, key_type):
    """
    Retrieve the latest API key for a given user and key_type.
    If no personal key exists, you might later extend this to
    check for a shared/team key.
    """
    try:
        # Assumes a user may have multiple keys; adjust as needed.
        # Here we return the most recently created key.
        return APIKey.objects.filter(user=user, key_type=key_type).latest('created_at')
    except ObjectDoesNotExist:
        return None

def create_or_update_user_api_key(user, key_type, raw_key, encryption_key_id):
    """
    Create or update an API key for the given user and key_type.
    The 'raw_key' should be processed by the EncryptedCharField automatically.
    """
    # Update or create a new API key entry
    key_obj, created = APIKey.objects.update_or_create(
        user=user,
        key_type=key_type,
        defaults={
            'encrypted_key': raw_key,           # EncryptedCharField will handle encryption
            'encryption_key_id': encryption_key_id,
        }
    )
    return key_obj

def delete_user_api_key(user, key_type):
    """
    Delete an API key for the given user and key_type.
    """
    qs = APIKey.objects.filter(user=user, key_type=key_type)
    deleted_count, _ = qs.delete()
    return deleted_count

# Optional: Additional helper function to rotate keys
def rotate_user_api_key(user, key_type, new_raw_key, new_encryption_key_id):
    """
    Rotate an API key for the user by updating it to a new key value.
    """
    return create_or_update_user_api_key(user, key_type, new_raw_key, new_encryption_key_id)
