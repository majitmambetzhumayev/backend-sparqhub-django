from django.core.management.base import BaseCommand

from mcp_server.server import mcp


class Command(BaseCommand):
    help = "Start the SparqHub MCP server (STDIO transport)"

    def handle(self, *args, **options):
        mcp.run()
