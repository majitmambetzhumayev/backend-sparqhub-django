# mcp_client/serializers.py
import ipaddress
import socket
from urllib.parse import urlparse

from rest_framework import serializers
from projects.models import Project
from .models import MCPServer


def _is_safe_sse_url(url: str) -> bool:
    """Rejects SSE MCP server URLs that would make this backend's own network
    stack connect to a non-public host (cloud metadata endpoints, internal
    services, loopback, etc.) — the stdio transport running a user's own
    command is an accepted feature, but SSE means *this server* makes the
    outbound connection, on the user's behalf, to wherever they point it."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return False
    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False
    for *_, sockaddr in addrinfo:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


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
        if transport == 'sse':
            if not url:
                raise serializers.ValidationError({'url': 'Required for sse transport.'})
            if not _is_safe_sse_url(url):
                raise serializers.ValidationError({'url': 'This URL is not allowed.'})
        return attrs
