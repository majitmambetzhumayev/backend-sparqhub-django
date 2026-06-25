from mcp.server.fastmcp import FastMCP

mcp = FastMCP("SparqHub")


@mcp.tool()
def list_assistants(user_id: int) -> list[dict]:
    """List all active assistants for a user."""
    from assistants.models import Assistant
    rows = Assistant.objects.filter(user_id=user_id, deleted=False).values(
        "id", "name", "ai_provider", "model"
    )
    return list(rows)


@mcp.tool()
def list_threads(user_id: int, assistant_id: int | None = None) -> list[dict]:
    """List conversation threads for a user, optionally filtered by assistant."""
    from threads.models import Thread
    qs = Thread.objects.filter(user_id=user_id).select_related("assistant")
    if assistant_id is not None:
        qs = qs.filter(assistant_id=assistant_id)
    return [
        {"id": t.id, "assistant": t.assistant.name, "created_at": t.created_at.isoformat()}
        for t in qs.order_by("-created_at")[:50]
    ]


@mcp.tool()
def get_thread_messages(thread_id: int, limit: int = 20) -> list[dict]:
    """Get messages in a conversation thread."""
    from chat_messages.models import Message
    messages = Message.objects.filter(thread_id=thread_id).order_by("timestamp")[:limit]
    return [
        {"sender": m.sender, "content": m.content, "timestamp": m.timestamp.isoformat()}
        for m in messages
    ]


@mcp.tool()
def search_memories(user_id: int, query: str) -> list[str]:
    """Semantically search a user's stored memories."""
    from users.models import CustomUser
    from librarian.services import retrieve_relevant_memories
    user = CustomUser.objects.get(pk=user_id)
    return retrieve_relevant_memories(user, query)
