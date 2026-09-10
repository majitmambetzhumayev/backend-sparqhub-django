# changelog/migrations/0003_seed_observability_entry.py
from datetime import datetime, timezone as dt_timezone

from django.db import migrations

# See 0002_seed_initial_entries.py for the pattern/rationale -- hand-curated,
# user-facing framing, not raw commit messages.
ENTRIES = [
    {
        "published_at": datetime(2026, 9, 10, tzinfo=dt_timezone.utc),
        "title_en": "Better behind-the-scenes monitoring",
        "title_fr": "Un meilleur suivi en coulisses",
        "description_en": (
            "We added more visibility into how conversations run under the hood, so we can "
            "catch and fix issues faster."
        ),
        "description_fr": (
            "On a ajouté plus de visibilité sur ce qui se passe pendant vos conversations, pour "
            "repérer et corriger les problèmes plus vite."
        ),
        "commit_refs": "backend (finish_reason span, memory dedup)",
    },
]


def seed_entries(apps, schema_editor):
    ChangelogEntry = apps.get_model('changelog', 'ChangelogEntry')
    ChangelogEntry.objects.bulk_create([ChangelogEntry(**entry) for entry in ENTRIES])


def remove_seeded_entries(apps, schema_editor):
    ChangelogEntry = apps.get_model('changelog', 'ChangelogEntry')
    ChangelogEntry.objects.filter(commit_refs__in=[e["commit_refs"] for e in ENTRIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('changelog', '0002_seed_initial_entries'),
    ]

    operations = [
        migrations.RunPython(seed_entries, remove_seeded_entries),
    ]
