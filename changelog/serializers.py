# changelog/serializers.py
from rest_framework import serializers

from .models import ChangelogEntry


class ChangelogEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ChangelogEntry
        fields = ['id', 'title_fr', 'title_en', 'description_fr', 'description_en', 'published_at']
