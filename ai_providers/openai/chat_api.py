# openai/chat_api.py
import logging
from agents import Runner, Agent

logger = logging.getLogger(__name__)

def chat_with_agent(assistant, message_text, stream=False):
    """
    Sends a message to an agent constructed from the local Assistant record.
    
    If stream is True, returns a generator yielding chunks.
    Otherwise, returns a list with the final result.
    """
    try:
        # Instantiate an Agent using local assistant data.
        agent = Agent(
            name=assistant.name,
            instructions=assistant.instructions,
            model=assistant.model
        )
    except Exception as e:
        logger.exception("Failed to instantiate agent from assistant: %s", e)
        raise e

    if stream:
        # The SDK should return a streaming generator.
        # (Adjust this if the streaming API changes.)
        return agent.chat(message_text, stream=True)
    else:
        result = Runner.run_sync(agent, message_text)
        return [result]  # Wrap in a list for uniformity
