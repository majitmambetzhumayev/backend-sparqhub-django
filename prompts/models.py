# prompts/models.py
from django.db import models


class PromptTemplate(models.Model):
    """Versioned, DB-backed prompt content -- never generated/derived, always
    hand-written and reviewed like any other prompt, just editable from the
    admin without a deploy. `name` identifies the prompt's purpose (e.g.
    'memory_extraction_system'); multiple rows can share a name, one per
    version, with exactly one active at a time (enforced in save() below)."""
    name = models.SlugField(max_length=100)
    version = models.PositiveIntegerField(default=1)
    content = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']
        constraints = [
            models.UniqueConstraint(fields=['name', 'version'], name='unique_version_per_prompt_name'),
        ]

    def __str__(self):
        return f"{self.name} v{self.version}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            # Enforced here (not just in the admin) so the invariant holds
            # regardless of entry point -- shell, a future API, etc.
            PromptTemplate.objects.filter(name=self.name).exclude(pk=self.pk).update(is_active=False)
