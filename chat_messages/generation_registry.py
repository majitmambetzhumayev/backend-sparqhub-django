# chat_messages/generation_registry.py
"""Tracks in-flight chat generations independent of any one WebSocket
connection, so a dropped connection (network blip, transient Redis error
crashing that connection's dispatch loop, a closed tab) doesn't cancel the
generation itself — see ConversationConsumer.disconnect().

Plain in-process dict: safe because this service runs as a single ASGI
process per instance (Dockerfile's CMD runs bare `uvicorn ...` with no
--workers flag, and no autoscaling is configured on Render). If this is ever
scaled to multiple workers/instances, this registry stops being
authoritative across them (group broadcast would still work fine, being
Redis-backed via channels_redis — only the claim/lock semantics here would
need to move to Redis too). Not building that now.
"""
import asyncio
from dataclasses import dataclass


@dataclass
class _PendingConfirmation:
    future: asyncio.Future
    tool: str
    arguments: dict


@dataclass
class _Generation:
    task: asyncio.Task | None = None
    pending_confirmation: "_PendingConfirmation | None" = None


_active: dict[int, _Generation] = {}


def try_claim(thread_id: int) -> bool:
    """Synchronous check-and-reserve, no `await` inside — must be called
    directly in ConversationConsumer.receive() before any await/create_task,
    so no other coroutine can interleave between the check and the
    reservation. Fragile to future edits: inserting an await into receive()
    before the try_claim/create_task pair silently breaks this guarantee."""
    if thread_id in _active:
        return False
    _active[thread_id] = _Generation()
    return True


def attach_task(thread_id: int, task: asyncio.Task) -> None:
    _active[thread_id].task = task


def is_active(thread_id: int) -> bool:
    return thread_id in _active


def set_pending_confirmation(thread_id: int, future: asyncio.Future, tool: str, arguments: dict) -> None:
    gen = _active.get(thread_id)
    if gen is not None:
        gen.pending_confirmation = _PendingConfirmation(future=future, tool=tool, arguments=arguments)


def clear_pending_confirmation(thread_id: int) -> None:
    gen = _active.get(thread_id)
    if gen is not None:
        gen.pending_confirmation = None


def get_confirmation_future(thread_id: int) -> asyncio.Future | None:
    gen = _active.get(thread_id)
    return gen.pending_confirmation.future if gen is not None and gen.pending_confirmation is not None else None


def get_pending_confirmation(thread_id: int) -> _PendingConfirmation | None:
    """The tool/arguments a still-active generation is waiting on approval
    for, if any — used by ConversationConsumer._join_thread to re-broadcast
    the confirm_required prompt to a client that (re)joins after it was
    already sent once, rather than leaving them looking at a generic
    "resuming" with no way to actually answer it."""
    gen = _active.get(thread_id)
    return gen.pending_confirmation if gen is not None else None


def release(thread_id: int) -> None:
    _active.pop(thread_id, None)
