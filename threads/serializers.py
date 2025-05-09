#threads/serializers.py
from rest_framework import serializers
from .models import Thread

class ThreadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Thread
        fields = [
            'id',
            'assistant',
            'conversation_state',
            'created_at',
            'updated_at',
        ]
