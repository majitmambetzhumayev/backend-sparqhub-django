#chat_messages/models.py
from django.db import models
from threads.models import Thread

SENDER_CHOICES = (
    ('user', 'User'),
    ('assistant', 'Assistant'),
)

class Message(models.Model):
    thread = models.ForeignKey(
        Thread,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    sender = models.CharField(max_length=10, choices=SENDER_CHOICES)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    edited = models.BooleanField(default=False)
    read = models.BooleanField(default=False)
    # Ordered tool names used while generating this message (assistant rows
    # only — always [] for 'user' rows). Lets the frontend show which steps
    # produced a given answer, including after a page reload.
    tool_calls = models.JSONField(default=list, blank=True)
    # Token usage for this turn (assistant rows only — always 0 for 'user'
    # rows). ai_providers.base.UsageAccumulator already computes these per
    # turn to price the credit deduction, but previously discarded them
    # once that was done; persisted here so usage can be aggregated (e.g.
    # for a dashboard summary) without re-deriving it from provider calls.
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    # Real USD cost, computed from ai_providers PRICING tables -- only set
    # when a personal (BYOK) key was used. Global-key turns deduct credits
    # instead (see ai_providers.chat_router.deduct_credits) and leave this
    # at 0, since that spend isn't paid directly by the user at the
    # provider and is already tracked via credits_remaining.
    estimated_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    def __str__(self):
        return f"Message {self.id} in Thread {self.thread.id}"


class PendingTurn(models.Model):
    """A durability net around generation_registry's in-memory turn state —
    still not *exact-point* resume (see chat_messages/services.py's
    run_and_broadcast_turn and ORCHESTRATION.md: a provider's native
    tool-call response isn't serializable in a provider-agnostic way, so a
    crash mid-tool-loop can't be picked back up exactly where it left off),
    but as of ConversationConsumer._join_thread's auto-replay path, a
    process restart mid-turn is no longer always a dead end: when nothing
    could have had a side effect yet (see `tool_calls` below), the whole
    turn is safely replayed automatically on reconnect rather than just
    telling the client to resend it. When a tool call *was* proposed,
    it's still surfaced as a clear "please check, then resend" instead of
    silently vanishing (nothing is written to Message until the whole turn
    completes, so today there's no other trace of it at all).

    Started as a narrower model (PendingToolConfirmation) covering only the
    tool-confirmation-wait window; generalized to span the whole turn once
    it became clear a crash *outside* that window (e.g. mid-stream, no
    tool call involved) left a reconnecting client with no signal
    whatsoever, not even the "interrupted" message this model exists to
    provide.

    Written at the very start of run_and_broadcast_turn, deleted as soon as
    _record_turn persists the turn's Message rows (not left to the later
    `finally`, which would leave a gap between "saved" and "row gone" wide
    enough for a crash in that window to trigger a duplicate auto-replay —
    see _record_turn) or, for every other outcome (stopped, errored,
    insufficient credits), in that `finally` as the catch-all. At most one
    row per thread at any instant, by construction
    (generation_registry.try_claim already prevents two concurrent turns on
    the same thread)."""
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name='pending_turns')
    user_text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    # Ordered tool names proposed so far this turn (kept in sync with
    # run_and_broadcast_turn's local tool_calls list via track_tool_call).
    # An empty list is the safety signal a reconnecting client's stale row
    # is auto-replayed on: on_tool_call fires *before* a tool's confirmation
    # gate (see AgentTool's docstring), so this can't distinguish "actually
    # ran" from "merely proposed, maybe declined" -- deliberately
    # conservative: ANY entry here, confirmed or not, disqualifies
    # auto-replay, since we can't yet prove nothing ran. See
    # ConversationConsumer._join_thread.
    tool_calls = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"PendingTurn on Thread {self.thread_id}"
