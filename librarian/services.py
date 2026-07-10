from asgiref.sync import sync_to_async
from pgvector.django import CosineDistance

from ai_providers.factory import provider_session
from core.embeddings import embed as _embed, get_embed_client as _get_embed_client

from .models import MemoryEntry

_EXTRACTION_SYSTEM_PROMPT = (
    "Given one exchange between a user and an assistant, decide whether it contains any "
    "durable fact worth remembering about the user long-term (preferences, personal details, "
    "ongoing projects, decisions). Reply with one fact per line, each rewritten as a short "
    "standalone statement. If there is nothing worth remembering, reply with exactly NONE."
)

# Fixed to one provider/model regardless of a thread's chosen chat provider —
# every user's memories must land in the same vector space to be comparable
# via CosineDistance, and every embedding consumer (memories, project file
# chunks) shares the same client/model — see core/embeddings.py. Paid for
# on the app's own Mistral key, not a user's BYOK key or credits — this is
# internal infra, not a user-requested AI response.
_TOP_K = 5


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
