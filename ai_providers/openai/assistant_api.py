# ai_providers/openai/assistant_api.py
import logging
import uuid
from agents import Agent
from assistants.models import Assistant

logger = logging.getLogger(__name__)

def create_agent_and_record(user, name, instructions, model="gpt-4o"):
    """
    Creates an Agent instance using the OpenAI Agents SDK and records the assistant locally.
    If the Agent instance does not provide an ID, we generate one.
    """
    try:
        agent = Agent(
            name=name,
            instructions=instructions,
            model=model
        )
    except Exception as e:
        logger.exception("Failed to create agent: %s", e)
        raise e

    # If agent.id is None or falsy, generate a new unique ID.
    if not getattr(agent, "id", None):
        agent.id = "agent_" + str(uuid.uuid4())

    assistant = Assistant.objects.create(
        user=user,
        name=name,
        instructions=instructions,
        model=model,
        ai_provider="openai",
        provider_assistant_id=agent.id,  # Now guaranteed to be non-null
        metadata={"tools": getattr(agent, "tools", []), "files": getattr(agent, "files", [])}
    )
    return assistant

def soft_delete_local(assistant_id, user):
    """
    Performs a soft delete of the assistant in the local database.
    """
    assistant = Assistant.objects.get(provider_assistant_id=assistant_id, user=user)
    assistant.deleted = True
    assistant.save()
    return assistant
