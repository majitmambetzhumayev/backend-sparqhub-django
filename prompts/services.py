# prompts/services.py
from .models import PromptTemplate


def get_active_prompt(name: str) -> str:
    """Raises PromptTemplate.DoesNotExist if no active version exists for
    this name -- callers should let this raise loudly (a missing prompt is
    a real configuration error) rather than silently falling back to
    something that might be stale or wrong."""
    return PromptTemplate.objects.get(name=name, is_active=True).content
