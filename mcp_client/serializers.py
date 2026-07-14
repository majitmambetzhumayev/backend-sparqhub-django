# mcp_client/serializers.py
from rest_framework import serializers
from projects.models import Project
from .models import MCPServer
from .services import is_safe_sse_url


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
        if transport == 'stdio':
            # stdio runs `command`/`args` as a subprocess on THIS backend,
            # not the requesting user's machine — _get_mcp_context() spawns
            # it on every chat turn in the project just to enumerate tools,
            # unconditional on the LLM ever choosing to call one. Letting
            # any authenticated user set an arbitrary command here is
            # unauthenticated-adjacent remote code execution against the
            # backend's own environment (DB credentials, provider API keys,
            # everything else in settings). Restricted to staff, who are
            # trusted to pre-configure specific tool servers.
            request = self.context.get('request')
            if not (request and request.user.is_staff):
                raise serializers.ValidationError(
                    {'transport': 'stdio transport is restricted to staff.'}
                )
            if not command:
                raise serializers.ValidationError({'command': 'Required for stdio transport.'})
        if transport == 'sse':
            if not url:
                raise serializers.ValidationError({'url': 'Required for sse transport.'})
            if not is_safe_sse_url(url):
                raise serializers.ValidationError({'url': 'This URL is not allowed.'})
        return attrs
