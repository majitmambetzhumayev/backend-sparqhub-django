from dataclasses import dataclass, field


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

    @property
    def requires_tool_execution(self) -> bool:
        return bool(self.tool_calls)


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
