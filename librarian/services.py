from asgiref.sync import sync_to_async
from pgvector.django import CosineDistance

from ai_providers.factory import provider_session
from core.embeddings import embed as _embed, get_embed_client as _get_embed_client
from prompts.services import get_active_prompt

from .models import MemoryEntry

_EXTRACTION_PROMPT_NAME = 'memory_extraction_system'

# Fixed to one provider/model regardless of a thread's chosen chat provider —
# every user's memories must land in the same vector space to be comparable
# via CosineDistance, and every embedding consumer (memories, project file
# chunks) shares the same client/model — see core/embeddings.py. Paid for
# on the app's own Mistral key, not a user's BYOK key or credits — this is
# internal infra, not a user-requested AI response.
_TOP_K = 5

# Cosine distance below this is treated as "the same fact restated", not a
# new one — e.g. "Allergic to peanuts." vs "Is allergic to peanuts" should
# collapse, but two genuinely different facts shouldn't. Deliberately
# conservative (only very close paraphrases collapse): merging two
# different facts would be worse than occasionally missing a duplicate.
# Not empirically tuned against real usage yet, same caveat as
# project_files' chunk size default.
_DEDUP_MAX_DISTANCE = 0.05


def _find_duplicate(user, embedding) -> MemoryEntry | None:
    return (
        MemoryEntry.objects
        .filter(user=user)
        .annotate(distance=CosineDistance('embedding', embedding))
        .filter(distance__lte=_DEDUP_MAX_DISTANCE)
        .order_by('distance')
        .first()
    )


def store_memory(user, content: str) -> MemoryEntry:
    embedding = _embed(content)
    # Restating the same fact across turns previously created a duplicate
    # row every time (see REVIEW.md) — extraction runs per turn with no
    # memory of what it already stored, so this is the only place that can
    # catch it. Returns the existing entry unchanged rather than touching
    # its created_at; ordering/retrieval is by embedding similarity, not
    # recency, so there's nothing to gain from bumping it.
    duplicate = _find_duplicate(user, embedding)
    if duplicate is not None:
        return duplicate
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
    system_prompt = await sync_to_async(get_active_prompt)(_EXTRACTION_PROMPT_NAME)
    async with provider_session(assistant.ai_provider, api_key=api_key) as provider:
        response = await provider.complete(assistant, messages, system_prompt, None)
    for line in response.text.splitlines():
        fact = line.strip()
        if fact and fact.upper() != "NONE":
            await sync_to_async(store_memory)(user, fact)
