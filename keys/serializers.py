from rest_framework import serializers
from assistants.models import AI_PROVIDER_CHOICES
from .models import APIKey

class APIKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = APIKey
        # encrypted_key is deliberately excluded: EncryptedCharField decrypts
        # transparently on read, so including it would return the raw key.
        fields = ['id', 'key_type', 'encryption_key_id', 'created_at', 'updated_at']


class APIKeyWriteSerializer(serializers.Serializer):
    key_type = serializers.ChoiceField(choices=AI_PROVIDER_CHOICES)
    raw_key = serializers.CharField(write_only=True, trim_whitespace=True)
