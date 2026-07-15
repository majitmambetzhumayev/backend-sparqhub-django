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
    # Nothing is persisted to the DB mid-turn (_record_turn only runs once
    # the whole reply is done) — these two fields are the only record of an
    # in-flight turn's progress, so a client that (re)joins mid-stream (e.g.
    # navigated to another thread and back while this one was still
    # generating) can be caught up instead of seeing neither the question
    # nor the answer-so-far until the turn eventually finishes.
    user_text: str = ""
    streamed_text: str = ""


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


def set_turn_text(thread_id: int, user_text: str) -> None:
    gen = _active.get(thread_id)
    if gen is not None:
        gen.user_text = user_text


def append_streamed_chunk(thread_id: int, chunk: str) -> None:
    gen = _active.get(thread_id)
    if gen is not None:
        gen.streamed_text += chunk


def get_turn_progress(thread_id: int) -> tuple[str, str]:
    """The user's message text and the assistant text streamed so far for a
    still-active generation — used by ConversationConsumer._join_thread so a
    client that (re)joins mid-turn sees what's already happened instead of
    an empty screen until the whole turn finishes and _record_turn saves it.
    Returns ("", "") if thread_id has no active generation."""
    gen = _active.get(thread_id)
    if gen is None:
        return "", ""
    return gen.user_text, gen.streamed_text


def is_active(thread_id: int) -> bool:
    return thread_id in _active


def get_task(thread_id: int) -> asyncio.Task | None:
    gen = _active.get(thread_id)
    return gen.task if gen is not None else None


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
