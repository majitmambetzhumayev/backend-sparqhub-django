import logging
import uuid
import os
from agents import Agent, Runner, input_guardrail, GuardrailFunctionOutput
from pydantic import BaseModel
from assistants.models import Assistant
from .sdk_utils import apply_openai_key

logger = logging.getLogger(__name__)

class UserInput(BaseModel):
    instructions: str
    model: str = "gpt-4o"
    
@input_guardrail()
def validate_user_input(ctx, item) -> GuardrailFunctionOutput:
    # pydantic validation will raise if it fails
    validated = UserInput.parse_obj(item)
    return GuardrailFunctionOutput(input=validated.dict())    

def create_agent_and_record(user, name, instructions, model="gpt-4o"):
    """
    Creates an Agent instance using the OpenAI Agents SDK with guardrails,
    tracing, and handoffs; then records the assistant locally.
    """
    # Ensure OPENAI_API_KEY is set for this process
    apply_openai_key(user)

    # Define a handoff helper agent, if desired:
    helper = Agent(
        name="MemoryButler",
        instructions="Manage long‑term memory entries",
        model=model,
    )

    # Instantiate the primary agent with guardrails (dict), tracing, and handoffs
    agent = Agent(
        name=name,
        instructions=instructions,
        model=model,
        input_guardrails=[validate_user_input],
        handoffs=[helper],
    )

    # If SDK didn't assign an ID, generate one
    if not getattr(agent, "id", None):
        agent.id = "agent_" + str(uuid.uuid4())

    # Persist locally
    assistant = Assistant.objects.create(
        user=user,
        name=name,
        instructions=instructions,
        model=model,
        ai_provider="openai",
        provider_assistant_id=agent.id,
        metadata={
            "tools": getattr(agent, "tools", []),
            "trace_enabled": True
        }
    )

    logger.info("Created assistant: %s (%s)", assistant.name, assistant.provider_assistant_id)
    return assistant

def soft_delete_local(assistant_id, user):
    """
    Soft‑delete the assistant record locally.
    """
    assistant = Assistant.objects.get(provider_assistant_id=assistant_id, user=user)
    assistant.deleted = True
    assistant.save()
    logger.info("Soft‑deleted assistant: %s", assistant_id)
    return assistant
