from django.db import models
from pgvector.django import VectorField


class ProjectFile(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('ready', 'Ready'),
        ('failed', 'Failed'),
    ]

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='files',
    )
    original_filename = models.CharField(max_length=255)
    # Self-assigned from the filename extension at upload time, not trusted
    # from the browser-supplied Content-Type header (unreliable/empty for
    # .md across browsers) — see project_files/services.py::resolve_canonical_content_type.
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    storage_key = models.CharField(max_length=500)
    thumbnail_storage_key = models.CharField(max_length=500, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.original_filename} ({self.status}) → {self.project.name}"


class ProjectFileChunk(models.Model):
    file = models.ForeignKey(ProjectFile, on_delete=models.CASCADE, related_name='chunks')
    # Denormalized, mirroring MemoryEntry.user — the search tool queries
    # per-project directly, avoiding a join through file__project on every
    # chat turn that calls search_project_files.
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='file_chunks',
    )
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    # 1024-dim matches Mistral's mistral-embed, same as librarian.MemoryEntry.
    embedding = VectorField(dimensions=1024)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['file_id', 'chunk_index']
        constraints = [
            models.UniqueConstraint(fields=['file', 'chunk_index'], name='unique_chunk_index_per_file'),
        ]

    def __str__(self):
        return f"{self.file.original_filename} chunk {self.chunk_index}"
