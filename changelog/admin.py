# changelog/admin.py
from django.contrib import admin

from .models import ChangelogEntry


@admin.register(ChangelogEntry)
class ChangelogEntryAdmin(admin.ModelAdmin):
    list_display = ['title_en', 'published_at', 'commit_refs']
    ordering = ['-published_at']
