# mcp_client/serializers.py
from rest_framework import serializers
from projects.models import Project
from .models import MCPServer


class MCPServerSerializer(serializers.ModelSerializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.none())

    class Meta:
        model = MCPServer
        fields = [
            'id',
            'project',
            'name',
            'transport',
            'url',
            'command',
            'args',
            'enabled',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request:
            self.fields['project'].queryset = Project.objects.filter(user=request.user)

    def validate(self, attrs):
        transport = attrs.get('transport', getattr(self.instance, 'transport', 'stdio'))
        command = attrs.get('command', getattr(self.instance, 'command', ''))
        url = attrs.get('url', getattr(self.instance, 'url', ''))
        if transport == 'stdio' and not command:
            raise serializers.ValidationError({'command': 'Required for stdio transport.'})
        if transport == 'sse' and not url:
            raise serializers.ValidationError({'url': 'Required for sse transport.'})
        return attrs
