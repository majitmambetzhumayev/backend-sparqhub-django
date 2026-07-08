from functools import lru_cache

from asgiref.sync import sync_to_async
from django.conf import settings
from mistralai.client import Mistral
from pgvector.django import CosineDistance

from ai_providers.factory import provider_session

from .models import MemoryEntry

_EXTRACTION_SYSTEM_PROMPT = (
    "Given one exchange between a user and an assistant, decide whether it contains any "
    "durable fact worth remembering about the user long-term (preferences, personal details, "
    "ongoing projects, decisions). Reply with one fact per line, each rewritten as a short "
    "standalone statement. If there is nothing worth remembering, reply with exactly NONE."
)

# Fixed to one provider/model regardless of a thread's chosen chat provider —
# every user's memories must land in the same vector space to be comparable
# via CosineDistance. Paid for on the app's own Mistral key, not a user's
# BYOK key or credits — this is internal infra, not a user-requested AI
# response. mistral-embed doesn't support output_dimension truncation
# (confirmed via a live call, its docs are ambiguous on this) — outputs a
# fixed 1024 dims, matching MemoryEntry.embedding's VectorField.
_EMBED_MODEL = 'mistral-embed'
_TOP_K = 5


@lru_cache(maxsize=1)
def _get_embed_client() -> Mistral:
    return Mistral(api_key=settings.MISTRAL_API_KEY)


def _embed(text: str) -> list[float]:
    response = _get_embed_client().embeddings.create(model=_EMBED_MODEL, inputs=[text])
    return response.data[0].embedding


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


async def extract_and_store_memories(user, assistant, user_text: str, assistant_text: str) -> None:
    from keys.services import get_user_api_key

    key_record = await get_user_api_key(user, assistant.ai_provider)
    api_key = key_record.encrypted_key if key_record else None
    messages = [{"role": "user", "content": f"User: {user_text}\nAssistant: {assistant_text}"}]
    async with provider_session(assistant.ai_provider, api_key=api_key) as provider:
        response = await provider.complete(assistant, messages, _EXTRACTION_SYSTEM_PROMPT, None)
    for line in response.text.splitlines():
        fact = line.strip()
        if fact and fact.upper() != "NONE":
            await sync_to_async(store_memory)(user, fact)
