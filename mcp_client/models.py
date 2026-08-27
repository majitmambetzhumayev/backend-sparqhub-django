from django.db import models


class MCPServer(models.Model):
    TRANSPORT_CHOICES = [
        ('stdio', 'STDIO'),
        ('sse', 'SSE'),
    ]

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='mcp_servers',
    )
    name = models.CharField(max_length=100)
    transport = models.CharField(max_length=10, choices=TRANSPORT_CHOICES, default='stdio')
    url = models.CharField(max_length=500, blank=True)
    command = models.CharField(max_length=255, blank=True)
    args = models.JSONField(default=list)
    enabled = models.BooleanField(default=True)
    # Defaults to True (secure by default): an MCP server's tools run with
    # whatever privileges its own backend grants (e.g. a SQL-capable tool),
    # driven entirely by what the model decides to call — including content
    # it read from an untrusted source, like a project file, in this same
    # turn (see search_project_files' RAG results feeding straight into the
    # model's context). Requiring human confirmation before execution is the
    # only thing standing between "attacker-controlled document text" and
    # "attacker-controlled tool call." Opt out per-server for tools that are
    # read-only / low-risk and where the confirmation prompt is just friction.
    requires_confirmation = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.transport}) → {self.project.name}"
