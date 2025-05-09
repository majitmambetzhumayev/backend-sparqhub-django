#assistant/services.py
import logging
from .models import Assistant
from ai_providers.factory import get_provider

logger = logging.getLogger(__name__)

def create_assistant(user, name, instructions, model="gpt-4o", ai_provider="openai"):
    """
    Creates a new assistant record in the database.
    Note: OpenAI Agents SDK does not support persistent CRUD, so this is local only.
    """
    assistant = Assistant.objects.create(
        user=user,
        name=name,
        instructions=instructions,
        model=model,
        ai_provider=ai_provider
    )
    logger.info(f"Created assistant: {assistant.name} (ID: {assistant.id})")
    return assistant

def update_assistant(assistant_id, user, name=None, instructions=None, model=None):
    """
    Updates an assistant's details in the database.
    Since OpenAI Agents SDK does not support updating agents, this only affects local records.
    """
    try:
        assistant = Assistant.objects.get(id=assistant_id, user=user)
        if name:
            assistant.name = name
        if instructions:
            assistant.instructions = instructions
        if model:
            assistant.model = model
        assistant.save()
        logger.info(f"Updated assistant: {assistant.name} (ID: {assistant.id})")
        return assistant
    except Assistant.DoesNotExist:
        logger.error(f"Assistant ID {assistant_id} not found for user {user}")
        raise ValueError("Assistant not found")

def sync_assistants_from_provider(user, ai_provider="openai"):
    """
    Fetches all assistants belonging to the user from the database.
    OpenAI Agents SDK does not support listing or syncing, so this is local-only.
    """
    assistants = Assistant.objects.filter(user=user)
    logger.info(f"Synced {len(assistants)} assistants for user {user.id}")
    return list(assistants)
