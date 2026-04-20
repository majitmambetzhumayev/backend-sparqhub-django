import logging
from functools import lru_cache

from pgvector.django import CosineDistance
from sentence_transformers import SentenceTransformer

from .models import MemoryEntry

logger = logging.getLogger(__name__)

_MODEL_NAME = 'all-MiniLM-L6-v2'
_TOP_K = 5


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    return SentenceTransformer(_MODEL_NAME)


def _embed(text: str) -> list[float]:
    return _get_model().encode(text).tolist()


def store_memory(user, content: str) -> MemoryEntry:
    embedding = _embed(content)
    return MemoryEntry.objects.create(user=user, content=content, embedding=embedding)


def retrieve_relevant_memories(user, query: str, top_k: int = _TOP_K) -> list[str]:
    embedding = _embed(query)
    entries = (
        MemoryEntry.objects
        .filter(user=user)
        .order_by(CosineDistance('embedding', embedding))[:top_k]
    )
    return [entry.content for entry in entries]
