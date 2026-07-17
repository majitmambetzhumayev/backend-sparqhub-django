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
