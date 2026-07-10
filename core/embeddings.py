# core/embeddings.py
"""Shared Mistral embedding client, extracted from librarian/services.py so
project_files can reuse it rather than reaching into librarian's private
internals (its _embed/_get_embed_client were librarian-only, single-string
only — chunking a document needs a batched call so N chunks don't cost N
separate API round-trips).

Always uses the app's own settings.MISTRAL_API_KEY, decoupled from a
thread's chosen chat provider/BYOK key — every embedding consumer (memories,
file chunks) needs to land in one comparable vector space, and this is
internal infra cost, not billed to the user's credits (same as it already
wasn't for memory extraction)."""
from functools import lru_cache

from django.conf import settings
from mistralai.client import Mistral

# mistral-embed doesn't support output_dimension truncation (confirmed via
# a live call, its docs are ambiguous on this) — outputs a fixed 1024 dims,
# matching every VectorField that stores its output (MemoryEntry.embedding,
# ProjectFileChunk.embedding).
EMBED_MODEL = 'mistral-embed'
EMBED_DIMENSIONS = 1024


@lru_cache(maxsize=1)
def get_embed_client() -> Mistral:
    return Mistral(api_key=settings.MISTRAL_API_KEY)


def embed(text: str) -> list[float]:
    response = get_embed_client().embeddings.create(model=EMBED_MODEL, inputs=[text])
    return response.data[0].embedding


def embed_batch(texts: list[str]) -> list[list[float]]:
    """One API call for many texts — used when embedding a document's
    chunks, where embedding one at a time would mean one round-trip per
    chunk."""
    if not texts:
        return []
    response = get_embed_client().embeddings.create(model=EMBED_MODEL, inputs=texts)
    return [item.embedding for item in response.data]
