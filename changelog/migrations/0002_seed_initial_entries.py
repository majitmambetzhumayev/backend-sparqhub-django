# changelog/migrations/0002_seed_initial_entries.py
from datetime import datetime, timezone as dt_timezone

from django.db import migrations

# Hand-curated from the actual merged-PR history of both repos (not every
# commit -- only what a real end user would notice or care about). Dates
# approximate the week each feature actually shipped; commit_refs are a
# traceability note back to the real PRs, not a normalized relation -- see
# changelog/models.py.
ENTRIES = [
    {
        "published_at": datetime(2026, 7, 1, tzinfo=dt_timezone.utc),
        "title_en": "Long-term memory",
        "title_fr": "Mémoire long terme",
        "description_en": (
            "The assistant now remembers durable facts you share -- preferences, ongoing "
            "projects, decisions -- and brings them into new conversations automatically."
        ),
        "description_fr": (
            "L'assistant retient désormais les faits durables que tu partages -- préférences, "
            "projets en cours, décisions -- et les réutilise automatiquement dans tes nouvelles "
            "conversations."
        ),
        "commit_refs": "backend (pre-PR workflow, 2026-07-01)",
    },
    {
        "published_at": datetime(2026, 7, 6, tzinfo=dt_timezone.utc),
        "title_en": "A whole new way to chat",
        "title_fr": "Une toute nouvelle façon de discuter",
        "description_en": (
            "Real multi-turn conversations with Claude, GPT, Mistral, or Gemini -- switch "
            "provider or model anytime. Plus Projects to organize your work, custom tool "
            "connections (MCP), AI image generation, and the ability to hand a question off "
            "to a different model mid-conversation."
        ),
        "description_fr": (
            "De vraies conversations multi-tours avec Claude, GPT, Mistral ou Gemini -- change "
            "de provider ou de modèle à tout moment. Plus les Projets pour organiser ton "
            "travail, des connexions d'outils personnalisées (MCP), la génération d'images par "
            "IA, et la possibilité de transférer une question à un autre modèle en cours de "
            "route."
        ),
        "commit_refs": "backend#1, frontend#1",
    },
    {
        "published_at": datetime(2026, 7, 8, tzinfo=dt_timezone.utc),
        "title_en": "Sign in with Google or GitHub",
        "title_fr": "Connexion avec Google ou GitHub",
        "description_en": "Added social login, plus password reset and email confirmation by email.",
        "description_fr": "Ajout de la connexion sociale, plus la réinitialisation de mot de passe et la confirmation d'email.",
        "commit_refs": "backend#10,#12, frontend#6,#7,#8",
    },
    {
        "published_at": datetime(2026, 7, 9, tzinfo=dt_timezone.utc),
        "title_en": "Full French/English support",
        "title_fr": "Support complet français/anglais",
        "description_en": "Every corner of the app is now properly translated, not just the main screens.",
        "description_fr": "Toute l'app est désormais correctement traduite, pas seulement les écrans principaux.",
        "commit_refs": "frontend#15",
    },
    {
        "published_at": datetime(2026, 7, 10, tzinfo=dt_timezone.utc),
        "title_en": "See what's happening while the AI works",
        "title_fr": "Suis ce qui se passe pendant que l'IA travaille",
        "description_en": (
            "Live status while chatting -- thinking, using a tool, or handing off to another "
            "model -- plus a Stop button and automatic reconnection if your connection drops."
        ),
        "description_fr": (
            "Statut en direct pendant la conversation -- réflexion, utilisation d'un outil, ou "
            "transfert vers un autre modèle -- plus un bouton Stop et une reconnexion "
            "automatique en cas de coupure."
        ),
        "commit_refs": "backend#14,#15,#17,#18,#19, frontend#16,#17,#19,#20,#21,#22",
    },
    {
        "published_at": datetime(2026, 7, 13, tzinfo=dt_timezone.utc),
        "title_en": "Upload files to your Projects",
        "title_fr": "Ajoute des fichiers à tes Projets",
        "description_en": (
            "Add PDFs, Word docs, text files, and images to a Project -- the AI can search "
            "their content when relevant."
        ),
        "description_fr": (
            "Ajoute des PDF, docs Word, fichiers texte et images à un Projet -- l'IA peut "
            "chercher dedans quand c'est pertinent."
        ),
        "commit_refs": "backend#21,#22,#23,#24, frontend#24",
    },
    {
        "published_at": datetime(2026, 7, 16, tzinfo=dt_timezone.utc),
        "title_en": "New dashboard and homepage",
        "title_fr": "Nouveau tableau de bord et page d'accueil",
        "description_en": (
            "A real dashboard with your recent conversations, projects, and token usage at a "
            "glance -- plus a redesigned, cleaner homepage."
        ),
        "description_fr": (
            "Un vrai tableau de bord avec tes conversations récentes, tes projets et ta "
            "consommation de tokens en un coup d'œil -- plus une page d'accueil repensée, plus "
            "épurée."
        ),
        "commit_refs": "frontend#31,#33",
    },
    {
        "published_at": datetime(2026, 7, 16, 12, tzinfo=dt_timezone.utc),
        "title_en": "Mobile support",
        "title_fr": "Support mobile",
        "description_en": (
            "The app now works properly on your phone, with a real navigation menu instead of "
            "a squeezed-down desktop layout."
        ),
        "description_fr": (
            "L'app fonctionne désormais correctement sur mobile, avec un vrai menu de "
            "navigation plutôt qu'une mise en page desktop compressée."
        ),
        "commit_refs": "frontend#34",
    },
    {
        "published_at": datetime(2026, 7, 17, 9, tzinfo=dt_timezone.utc),
        "title_en": "Track what you spend with your own API key",
        "title_fr": "Suis tes dépenses avec ta propre clé API",
        "description_en": (
            "If you use your own provider API key, the dashboard now shows exactly what "
            "that's costing you in real dollars."
        ),
        "description_fr": (
            "Si tu utilises ta propre clé API, le tableau de bord affiche désormais précisément "
            "ce que ça te coûte en dollars réels."
        ),
        "commit_refs": "backend#44, frontend#35",
    },
    {
        "published_at": datetime(2026, 7, 17, 13, tzinfo=dt_timezone.utc),
        "title_en": "A quick tour on your first visit",
        "title_fr": "Un tour rapide à ta première visite",
        "description_en": "New accounts now get a short guided tour of the dashboard the first time they log in.",
        "description_fr": "Les nouveaux comptes ont désormais droit à un court tour guidé du tableau de bord dès la première connexion.",
        "commit_refs": "backend#45, frontend#37",
    },
    {
        "published_at": datetime(2026, 7, 17, 16, tzinfo=dt_timezone.utc),
        "title_en": "Smoother, more reliable replies",
        "title_fr": "Des réponses plus fluides et plus fiables",
        "description_en": (
            "Fixed a bug where the assistant's first reply could briefly flash away and "
            "reappear, and replies now type out smoothly instead of arriving in uneven chunks."
        ),
        "description_fr": (
            "Correction d'un bug où la première réponse de l'assistant pouvait disparaître puis "
            "réapparaître brièvement, et les réponses s'affichent désormais de façon fluide "
            "plutôt que par à-coups."
        ),
        "commit_refs": "frontend#39,#40",
    },
    {
        "published_at": datetime(2026, 7, 17, 17, tzinfo=dt_timezone.utc),
        "title_en": "Fixed sessions logging you out too early",
        "title_fr": "Correction des sessions déconnectées trop tôt",
        "description_en": (
            "You'll no longer get logged out every 15 minutes -- sessions now refresh silently "
            "in the background."
        ),
        "description_fr": (
            "Tu ne seras plus déconnecté(e) toutes les 15 minutes -- les sessions se "
            "renouvellent désormais silencieusement en arrière-plan."
        ),
        "commit_refs": "backend#46,#47, frontend#41",
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
        ('changelog', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_entries, remove_seeded_entries),
    ]
