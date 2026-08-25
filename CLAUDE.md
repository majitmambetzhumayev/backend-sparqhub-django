# SparqHub backend — CLAUDE.md

Django + Channels backend for SparqHub: multi-provider AI chat (Anthropic,
OpenAI, Mistral, Gemini), BYOK + credit metering, per-project MCP tools,
image generation, long-term memory. See `REVIEW.md` for what's actually been
verified vs. known gaps — keep that file updated as gaps close, don't
replace it wholesale.

## Code philosophy

The general rule (senior, clean, no speculative abstractions, comments
explain WHY not WHAT) lives in the user-level `CLAUDE.md` (outside this
repo, loaded automatically in every session) — don't re-duplicate it here.

Backend-specific instance of it: this codebase already has plenty of
WHY-comments (e.g. `chat_messages/consumers.py`, `ai_providers/base.py`'s
`finish_reason` note) — read them before assuming something is unexplained.

## Architecture: provider logic stays out of business logic

`ai_providers/` is a ports-and-adapters boundary, not just a folder of SDK
wrappers:

- `ai_providers/base.py` — the port. `AIProviderBase` (abstract contract:
  `complete`, `append_turn`, `stream`), `ProviderResponse`, `ToolCall`,
  `UsageAccumulator`.
- `ai_providers/{anthropic,openai,mistral,google}/` — adapters. Each one only
  knows how to translate to/from its own SDK's request/response shape.
- `ai_providers/agent_loop.py` — the tool-use loop, written once, provider-
  agnostic. This is the business logic.
- `ai_providers/factory.py` — picks the adapter for a given assistant/model.
- `ai_providers/chat_router.py` — dispatches a turn through the above.

**Rule: adding a provider means writing one new adapter that satisfies
`AIProviderBase` — never touching `agent_loop.py`.** If you find
provider-specific branching creeping into `agent_loop.py`, `chat_router.py`,
or anything outside a provider's own module, that's the abstraction leaking
and should be pushed back into the adapter.

The same instinct applies elsewhere in the codebase: `image_providers/` for
image generation, `mcp_client/` for external MCP tool servers — the pattern
is "swap the connector, keep the business logic ignorant of which one."

## App map

- `core/` — shared: embeddings, rate limiting, exceptions, middleware.
- `users/` — auth, `CustomUser`, OAuth (Google/GitHub).
- `threads/` — conversations (`Thread`), history/ordering.
- `chat_messages/` — `Message` model + the WebSocket consumer (streaming,
  tool-call pause/resume, per-thread frame scoping).
- `projects/` — groups threads; `SET_NULL` on delete (threads detach, don't
  cascade-delete).
- `assistants/` — configurable assistants (provider/model/system prompt).
- `keys/` — BYOK `APIKey` storage (encrypted via `FIELD_ENCRYPTION_KEY`).
- `mcp_client/` — per-project MCP tool server configs (`MCPServer`), stdio +
  SSE transports.
- `mcp_server/` — this project's own MCP server (`manage.py run_mcp_server`).
- `image_providers/` — `generate_image` tool, OpenAI + Gemini backends.
- `librarian/` — long-term memory extraction/storage (`MemoryEntry`).
- `prompts/` — versioned, DB-backed prompt registry.
- `project_files/` — uploaded file storage + chunking.
- `changelog/` — public patch-notes API backing the landing page.

## Testing

- `pytest` (see `pytest.ini`), Django settings auto-loaded. Test files:
  `tests.py` or `tests/test_*.py` per app.
- Prefer live-verification notes in `REVIEW.md` over trusting mocks alone for
  anything provider-facing — this codebase has a history of bugs that only
  showed up against the real SDK (see the Gemini `thought_signature` writeup
  in `REVIEW.md`).

## Env / secrets

See `.env.example` for the full variable list. Two that need care:

- `SECRET_KEY` and `FIELD_ENCRYPTION_KEY` must differ between dev and prod —
  never copy a dev value into a prod environment or vice versa.
  `FIELD_ENCRYPTION_KEY` in particular must never change in prod once data is
  encrypted with it, or that data becomes unreadable.
- LLM provider keys: use separate dev keys (low spend caps) from prod keys.

## Local dev (Docker)

`docker-compose.dev.yml`: `db` (pgvector/pg15) → `redis` → `migrate` (must
complete before `web` starts — this ordering exists because a migration
committed to the repo previously went silently unapplied, see the compose
file's comment) → `web` + `celery`.
