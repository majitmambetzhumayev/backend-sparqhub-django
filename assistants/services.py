#assistants/services.py
from django.db import IntegrityError

from .models import Assistant


def get_or_create_default_assistant(user) -> Assistant:
    try:
        assistant, _ = Assistant.objects.get_or_create(
            user=user, is_persistent=True, deleted=False,
            defaults={"name": "Default Assistant", "instructions": ""},
        )
    except IntegrityError:
        assistant = Assistant.objects.get(user=user, is_persistent=True, deleted=False)
    return assistant
