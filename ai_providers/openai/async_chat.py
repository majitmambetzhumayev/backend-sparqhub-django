# ai_providers/openai/async_chat.py
import logging
from agents import Agent, Runner

logger = logging.getLogger(__name__)

async def chat_async(assistant, message_text):
    """
    Runs the agent end‑to‑end and returns the final output as a string.
    Suitable for automation where streaming isn’t needed.
    """
    agent = Agent(
        name=assistant.name,
        instructions=assistant.instructions,
        model=assistant.model
    )
    # Runner.run is the async version; returns a RunResult
    result = await Runner.run(agent, message_text)
    return result.final_output
