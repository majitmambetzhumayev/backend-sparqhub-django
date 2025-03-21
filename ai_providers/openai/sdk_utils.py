# ai_providers/openai/sdk_utils.py
import os
from django.conf import settings

def apply_openai_key(user=None):
    """
    Sets the OPENAI_API_KEY environment variable from Django settings.
    Future extension: retrieve user-specific keys if 'user' is provided.
    """
    if not os.environ.get("OPENAI_API_KEY") and hasattr(settings, "OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY
