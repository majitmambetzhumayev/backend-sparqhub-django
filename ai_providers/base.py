import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ProviderResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: object = None  # provider's native response, needed by append_turn
    usage: dict | None = None  # {"input_tokens": int, "output_tokens": int}
    # Provider-native completion-status string (e.g. Anthropic's "end_turn"/
    # "max_tokens", OpenAI/Mistral's "stop"/"length"/"content_filter",
    # Google's "STOP"/"MAX_TOKENS"/"SAFETY") — deliberately left unnormalized
    # across providers rather than mapped to a shared enum, since the only
    # consumer today (run_agent_loop's truncation/content-filter check) just
    # needs to tell "ordinary completion" apart from "something cut this
    # off," not branch on the exact provider-specific reason.
    finish_reason: str | None = None

    @property
    def requires_tool_execution(self) -> bool:
        return bool(self.tool_calls)


# Values every provider uses for "the model stopped normally" (either with a
# plain answer or by requesting a tool call) — anything else (a token-limit
# cutoff, a content-filter block, etc.) is worth a trace, since nothing
# previously checked this at all: a truncated/blocked response was consumed
# exactly like a deliberate, complete one.
_BENIGN_FINISH_REASONS = {None, "stop", "end_turn", "tool_use", "tool_calls", "STOP"}


def warn_if_finish_reason_suspicious(response: "ProviderResponse") -> None:
    if response.finish_reason not in _BENIGN_FINISH_REASONS:
        logger.warning(
            "Model response finished with reason %r (possible truncation or content filtering)",
            response.finish_reason,
        )


@dataclass
class UsageAccumulator:
    """Mutable sink threaded by reference through complete()/stream()/run_agent_loop()
    so a multi-turn tool-calling loop accumulates total usage across every model
    call in the turn, not just the last one.

    `extra_credits` accumulates costs from tools priced outside the main
    provider/model (e.g. image generation) so they're deducted together with
    the rest of the turn's usage, once, only after the turn actually
    succeeds — a tool that deducted eagerly would still charge the user even
    if a later step in the same turn fails and the turn is never persisted."""

    input_tokens: int = 0
    output_tokens: int = 0
    extra_credits: int = 0

    def add(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


class AIProviderBase:
    """
    Adapter contract: a provider only knows how to talk to its own SDK for a
    single turn. The tool-use loop itself is shared (ai_providers/agent_loop.py)
    so adding a provider never means re-implementing that loop.
    """

    label: str = ""

    async def aclose(self) -> None:
        """Release any resources this instance holds open (e.g. a persistent
        async HTTP client). No-op by default — override only if the
        underlying SDK actually needs it. A fresh provider is constructed per
        turn, and some SDKs (Gemini's) hold an async client that outlives the
        call if never closed; garbage-collecting it later, after a short-lived
        event loop (e.g. a Celery task run via async_to_sync) has already
        closed, raises "Event loop is closed" from the finalizer."""

    AVAILABLE_MODELS: list[dict] = []

    async def complete(self, assistant, messages, system, tools) -> ProviderResponse:
        raise NotImplementedError

    def append_turn(self, messages, response: ProviderResponse, tool_results=None) -> list[dict]:
        """Fold a model response (and any executed tool_results) into this provider's native message shape."""
        raise NotImplementedError

    def stream(
        self, assistant, messages, system, tools, tool_executor,
        usage: UsageAccumulator | None = None, on_tool_call=None,
    ):
        """Async generator yielding text chunks for one turn. `usage`, if given, is
        `.add()`-ed with this turn's token counts once the stream completes.
        `on_tool_call`, if given, is an async callback invoked with a tool's name
        right before it executes."""
        raise NotImplementedError
