#threads/serializers.py
from rest_framework import serializers
from projects.models import Project
from .models import Thread


class ThreadListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Thread
        fields = [
            'id',
            'ai_provider',
            'model',
            'title',
            'project',
            'created_at',
            'updated_at',
        ]


class ThreadDetailSerializer(serializers.ModelSerializer):
    project_name = serializers.SerializerMethodField()

    class Meta:
        model = Thread
        fields = [
            'id',
            'ai_provider',
            'model',
            'title',
            'project',
            'project_name',
            'created_at',
            'updated_at',
        ]

    def get_project_name(self, obj):
        return obj.project.name if obj.project else None


class ThreadUpdateSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.none(), required=False, allow_null=True)

    class Meta:
        model = Thread
        fields = [
            'ai_provider',
            'model',
            'title',
            'project',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request:
            self.fields['project'].queryset = Project.objects.filter(user=request.user)
