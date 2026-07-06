#projects/serializers.py
from rest_framework import serializers
from .models import Project


class ProjectSerializer(serializers.ModelSerializer):
    thread_count = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            'id',
            'name',
            'description',
            'thread_count',
            'created_at',
            'updated_at',
        ]

    def get_thread_count(self, obj):
        # list/retrieve go through the annotated queryset; create/update
        # serialize the bare saved instance, which isn't annotated
        return obj.thread_count if hasattr(obj, 'thread_count') else obj.threads.count()
