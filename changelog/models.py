# changelog/models.py
from django.db import models
from django.utils import timezone


class ChangelogEntry(models.Model):
    """Hand-written, one per notable user-facing change -- never generated
    from git history (commit messages are written for engineers, not end
    users). commit_refs is a freeform traceability note back to the actual
    commits/PRs this entry describes, not a normalized relation: a single
    entry commonly spans a backend + frontend PR pair, and this is written
    by hand alongside the entry itself anyway."""
    title_fr = models.CharField(max_length=200)
    title_en = models.CharField(max_length=200)
    description_fr = models.TextField(blank=True, default='')
    description_en = models.TextField(blank=True, default='')
    commit_refs = models.CharField(max_length=255, blank=True, default='')
    published_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-published_at']

    def __str__(self):
        return self.title_en
