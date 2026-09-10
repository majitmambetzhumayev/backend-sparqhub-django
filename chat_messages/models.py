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
    """A durability net around generation_registry's in-memory turn state,
    NOT a true resume mechanism — see chat_messages/services.py's
    run_and_broadcast_turn and ORCHESTRATION.md for what a real fix would
    need (the provider's tool-call response isn't serializable in a
    provider-agnostic way, and naively replaying a turn risks re-running
    side effects, e.g. double-charging credits). This model only exists so
    a process restart at ANY point during an in-flight turn — mid-stream,
    mid-tool-call, mid-confirmation-wait, anywhere — is surfaced to the
    client as a clear "please resend" instead of the turn silently
    vanishing (nothing is written to Message until the whole turn
    completes, so today there's no trace of it at all).

    Started as a narrower model (PendingToolConfirmation) covering only the
    tool-confirmation-wait window; generalized to span the whole turn once
    it became clear a crash *outside* that window (e.g. mid-stream, no
    tool call involved) left a reconnecting client with no signal
    whatsoever, not even the "interrupted" message this model exists to
    provide.

    Written at the very start of run_and_broadcast_turn, deleted in its
    `finally` regardless of how the turn ends (completed, stopped,
    errored, insufficient credits) — at most one row per thread at any
    instant, by construction (generation_registry.try_claim already
    prevents two concurrent turns on the same thread)."""
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name='pending_turns')
    user_text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"PendingTurn on Thread {self.thread_id}"
