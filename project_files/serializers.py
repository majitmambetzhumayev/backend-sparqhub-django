# project_files/serializers.py
from django.conf import settings
from rest_framework import serializers

from projects.models import Project

from .models import ProjectFile
from .services import build_storage_url, create_project_file, resolve_canonical_content_type


class ProjectFileSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.none())
    file = serializers.FileField(write_only=True)
    file_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = ProjectFile
        fields = [
            'id',
            'project',
            'file',
            'original_filename',
            'content_type',
            'size_bytes',
            'status',
            'error_message',
            'file_url',
            'thumbnail_url',
            'created_at',
        ]
        read_only_fields = [
            'id', 'original_filename', 'content_type', 'size_bytes', 'status', 'error_message', 'created_at',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request:
            self.fields['project'].queryset = Project.objects.filter(user=request.user)

    def get_file_url(self, obj: ProjectFile) -> str:
        return build_storage_url(obj.storage_key)

    def get_thumbnail_url(self, obj: ProjectFile) -> str | None:
        return build_storage_url(obj.thumbnail_storage_key) if obj.thumbnail_storage_key else None

    def validate_file(self, value):
        if value.size > settings.PROJECT_FILE_MAX_SIZE_BYTES:
            max_mb = settings.PROJECT_FILE_MAX_SIZE_BYTES // (1024 * 1024)
            raise serializers.ValidationError(f'File is too large (max {max_mb}MB).')
        try:
            resolve_canonical_content_type(value.name)
        except ValueError:
            raise serializers.ValidationError('Unsupported file type.')
        return value

    def create(self, validated_data):
        return create_project_file(validated_data['project'], validated_data['file'])
