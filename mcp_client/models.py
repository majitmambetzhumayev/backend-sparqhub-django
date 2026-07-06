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

    def __str__(self):
        return f"{self.name} ({self.transport}) → {self.project.name}"
