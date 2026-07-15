#threads/services.py
from asgiref.sync import sync_to_async

from assistants.services import get_or_create_default_assistant

from .models import Thread

_TITLE_SYSTEM_PROMPT = (
    "Generate a short, descriptive title (max 6 words, no quotes, no trailing "
    "punctuation) for a conversation that starts with the following exchange."
)


def get_or_create_thread(user, thread_id=None, ai_provider=None, model=None, project_id=None) -> Thread:
    if thread_id is not None:
        thread = Thread.objects.select_related('assistant').get(pk=thread_id, user=user)
        thread.assistant  # force-load the FK while still in a sync context
        return thread

    from projects.models import Project

    assistant = get_or_create_default_assistant(user)
    kwargs = {}
    if ai_provider:
        kwargs['ai_provider'] = ai_provider
    if model:
        kwargs['model'] = model
    if project_id:
        # .get() (not .filter().first()) deliberately — a project_id that
        # doesn't exist or belongs to another user must be rejected, not
        # silently dropped. It used to fall through here with the thread
        # simply created project-less, a 200 the caller had no way to tell
        # apart from "no project_id was ever sent."
        kwargs['project'] = Project.objects.get(pk=project_id, user=user)
    thread = Thread.objects.create(user=user, assistant=assistant, conversation_state=[], **kwargs)
    thread.assistant
    return thread


def update_thread_provider(thread: Thread, ai_provider: str, model: str) -> Thread:
    from ai_providers.factory import PROVIDERS

    provider_cls = PROVIDERS.get(ai_provider)
    if provider_cls is None:
        raise ValueError(f"Unsupported provider: {ai_provider}")
    if model not in {m["id"] for m in provider_cls.AVAILABLE_MODELS}:
        raise ValueError(f"Unsupported model '{model}' for provider '{ai_provider}'")
    thread.ai_provider = ai_provider
    thread.model = model
    thread.save(update_fields=["ai_provider", "model", "updated_at"])
    return thread


async def generate_and_store_title(thread: Thread, user_text: str, assistant_text: str) -> None:
    from types import SimpleNamespace
    from keys.services import get_user_api_key
    from ai_providers.factory import provider_session

    key_record = await get_user_api_key(thread.user, thread.ai_provider)
    api_key = key_record.encrypted_key if key_record else None
    turn = SimpleNamespace(model=thread.model, instructions="")
    messages = [{"role": "user", "content": f"User: {user_text}\nAssistant: {assistant_text}"}]
    async with provider_session(thread.ai_provider, api_key=api_key) as provider:
        response = await provider.complete(turn, messages, _TITLE_SYSTEM_PROMPT, None)
    title = response.text.strip().strip('"').strip("'")[:100]
    if title:
        await sync_to_async(Thread.objects.filter(pk=thread.pk).update)(title=title)
