from rest_framework import serializers

from .models import MemoryEntry


class MemoryEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = MemoryEntry
        fields = ['id', 'content', 'created_at']
        read_only_fields = ['id', 'created_at']
