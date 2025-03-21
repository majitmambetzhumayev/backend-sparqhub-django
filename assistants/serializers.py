from rest_framework import serializers
from .models import Assistant

class AssistantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assistant
        fields = [
            'id',
            'provider_assistant_id',
            'name',
            'instructions',
            'model',
            'metadata',
            'ai_provider',
            'is_persistent',
            'supports_crud',
            'deleted',
            'created_at',
            'last_used_at',
        ]
