# ai_providers/openai/streaming_chat.py
import logging
import asyncio
from agents import Agent, Runner
from openai.types.responses import ResponseTextDeltaEvent

logger = logging.getLogger(__name__)

async def stream_chat(assistant, message_text):
    """
    Async generator yielding text deltas as they're produced.
    Useful for direct chat UIs.
    """
    agent = Agent(
        name=assistant.name,
        instructions=assistant.instructions,
        model=assistant.model
    )
    # run_streamed returns a RunResultStreaming
    result_stream = Runner.run_streamed(agent, input=message_text)
    # stream_events() gives StreamEvent objects
    async for event in result_stream.stream_events():
        # filter for text-delta events
        if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
            yield event.data.delta
