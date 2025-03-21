# ai_providers/openai_auth.py

import os
from django.conf import settings

def apply_openai_key(user=None):
    """
    Sets the OPENAI_API_KEY environment variable for the OpenAI Agent SDK.

    - In dev mode (or default), uses global settings.
    - Later can fetch user-specific key if needed.
    """
    if user:
        # Future logic could look something like:
        # key = UserAPIKey.objects.get(user=user, key_type="openai").value
        # os.environ["OPENAI_API_KEY"] = key
        pass
    elif not os.environ.get("OPENAI_API_KEY") and hasattr(settings, "OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY
