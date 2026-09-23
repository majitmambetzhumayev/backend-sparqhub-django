# changelog/migrations/0004_seed_orchestration_entry.py
from datetime import datetime, timezone as dt_timezone

from django.db import migrations

# See 0002_seed_initial_entries.py for the pattern/rationale -- hand-curated,
# user-facing framing, not raw commit messages.
ENTRIES = [
    {
        "published_at": datetime(2026, 9, 23, tzinfo=dt_timezone.utc),
        "title_en": "Your assistants can now delegate to each other (early preview)",
        "title_fr": "Tes assistants peuvent maintenant se déléguer des tâches (première ébauche)",
        "description_en": (
            "Give an assistant a specialty (e.g. \"accounting\") and others can hand off a "
            "question to it automatically when it fits better, always with your confirmation "
            "first. This is a first working version, so expect rough edges."
        ),
        "description_fr": (
            "Donne une spécialité à un assistant (ex : \"comptabilité\") et les autres peuvent "
            "lui transférer une question automatiquement quand c'est plus pertinent, toujours "
            "avec ta confirmation avant. C'est une première version, il peut rester des "
            "aspérités."
        ),
        "commit_refs": "backend (delegate_to_agent), frontend (assistant role field, nav link, onboarding step)",
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
        ('changelog', '0003_seed_observability_entry'),
    ]

    operations = [
        migrations.RunPython(seed_entries, remove_seeded_entries),
    ]
