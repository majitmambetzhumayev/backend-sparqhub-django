# core/rate_limit.py
from django.core.cache import cache

# Fixed-window counter via Django's cache framework. Correct as-is under
# this app's current deployment (Dockerfile's CMD runs bare uvicorn with no
# --workers flag, no autoscaling on Render — same single-process assumption
# already documented in chat_messages/generation_registry.py, where the
# default LocMemCache backend is process-local). Would need a shared cache
# backend (e.g. django-redis) if this ever scales to multiple processes.


def check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """Returns True if this call is within the limit (and counts toward it),
    False if the limit is already exceeded for this window. Not a sliding
    window — a burst can occur right at a window boundary — but that's an
    acceptable approximation at this scale, not worth the complexity of a
    sliding log for an abuse-prevention budget rather than a hard quota."""
    added = cache.add(key, 1, timeout=window_seconds)  # atomic set-if-absent
    if added:
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        # Key expired between add() and incr() — a rare race, not worth
        # guarding further; treat as the start of a fresh window.
        cache.set(key, 1, timeout=window_seconds)
        return True
    return count <= limit
