# SparqHub backend — multi-agent orchestration (design)

**Status: design decision only, nothing here is implemented yet.** This
records the shape agreed on 2026-09-09 so building it later doesn't start
from a blank page — see `REVIEW.md`'s Model delegation section and
`CLAUDE.md`'s tool-registry section for the current, actually-shipped
starting point this builds on.

## Where this comes from

Today's only "agentic" mechanism beyond a single assistant's tool loop is
`delegate_to_model` (`ai_providers/chat_router.py`) — a manual, one-shot
escalation to a different *provider*, gated by the same confirmation
mechanism every tool now goes through (`_require_confirmation`, the
`AgentTool` registry). Multi-agent orchestration generalizes this: instead
of delegating to a different provider, delegate to a different **agent** —
and do it recursively.

## Chosen shape: supervisor pattern

An agent receives a task, decides whether to handle it itself or delegate
to another agent better suited to it, and (if it delegated) folds the
result back into its own response. Rejected alternatives and why:

- **Sequential pipeline** (fixed A→B→C order) — too rigid for open-ended
  chat; better suited to a structured-workflow feature, not the general
  case.
- **Parallel fan-out/fan-in** — real use case (e.g. searching several
  tools/files at once) but that's an optimization within a turn, not a
  multi-agent architecture on its own.
- **Peer-to-peer / group chat** (agents conversing with each other,
  AutoGen-style) — hardest to control, can loop, expensive. Not
  appropriate for a shipped product.
- **Nested hierarchical teams** — this is where the design below actually
  ends up, but as a natural consequence of supervisor + recursion, not
  chosen as a separate pattern up front.

## Core primitive: a node is role + context + executor

Whatever an agent delegates to must look the same from the delegator's
side, whether it's a single assistant or a whole team behind it:

- **role** — a short declared specialty (e.g. "accounting", "customer
  communication"). The supervisor picks a delegation target by matching
  the task to a role, never by a hardcoded identity/list. This is the same
  instinct behind the `AgentTool` registry (`_build_combined_executor`):
  one uniform interface, no special-casing per target, so adding a new
  node is adding data, not editing dispatch logic.
- **context** — instructions/system prompt, memory scope, and tool/MCP/
  project access, scoped to that node specifically rather than inherited
  wholesale from the outer conversation. An "accounting" node shouldn't
  see or pollute an unrelated casual-chat thread's context.
- **executor** — either a direct LLM call (leaf node), or its own
  supervisor loop over child nodes (a **pod**). Recursive: a pod can
  contain pods.

This is what lets "connect agents as nodes with precise roles, e.g. a
communication team or an accounting team" (the actual ask that produced
this design) fall out for free: a "team" is just a pod — from the outside
it's one node with a role and a context, like any other; internally it
runs its own supervisor over its own members. The parent supervisor never
needs to know whether it delegated to a single agent or an entire team.

## Concrete implication for the data model (when this gets built, not now)

- `Assistant` needs a `role`/`specialty` field so the supervisor has
  something to match against. Cheap to add now even before the rest
  exists; expensive to retrofit once delegation logic is written assuming
  a flat hardcoded list.
- A `Team`/pod grouping concept is the part to actually defer — don't
  build it speculatively. The point of the design above is that adding it
  later doesn't require restructuring the delegation interface, only
  introducing a new kind of node.

## Open questions to resolve during implementation, not now

- **Recursion depth limit** — `agent_loop.py` already bounds flat tool-call
  rounds via `MAX_TOOL_ITERATIONS`; nested pod delegation needs an
  equivalent bound so a misconfigured pod hierarchy can't recurse
  indefinitely.
- **Cost/credit attribution through nested delegation** — `deduct_credits`
  currently prices one turn; a request that fans out through several
  nested pods needs its cost rolled up correctly, not lost or
  double-counted.
- **LangGraph vs. hand-rolled** — still an explicitly deferred decision
  (see `CLAUDE.md`), but pods map directly onto LangGraph's *subgraphs*
  (a compiled graph embeddable as a single node in a parent graph) more
  closely than the earlier flat-delegation framing did. Worth
  re-evaluating once this is actually being built, not before.
- **Durable resume is still only partial.** `PendingTurn` (see `REVIEW.md`'s
  MCP integration section) now covers a crash anywhere in a turn's
  lifetime — mid-stream, mid-tool-call, mid-confirmation-wait — with a
  "please resend" signal, generalized from an earlier version that only
  covered the confirmation-wait window. It still doesn't *resume* the
  turn, only reports the interruption. Orchestration adds more ways a turn
  can be mid-flight (waiting on a nested pod, say) — `PendingTurn` should
  already cover those too (it's turn-level, not tied to any specific
  sub-state), but confirm that holds once pods actually exist rather than
  assuming it.

## How to apply

Read this file before implementing any part of multi-agent orchestration.
Keep it updated as decisions change — this is a living design doc, not a
one-time snapshot, same convention as `REVIEW.md`.
