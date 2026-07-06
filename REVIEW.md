# SparqHub backend — review log

Baseline audit before this branch merges to `main` and recurring-commit discipline
starts. Lists what exists, what's been verified and how, and what hasn't. Update
this file (don't replace it) as gaps get closed — it's meant to stay useful,
not to be a one-off snapshot.

**Automated test suite: 197/197 passing** (`pytest`, this branch). No known
flaky tests as of this review — a pre-existing order-dependent failure
(`asyncio.get_event_loop()` closing across test modules) was fixed in 8 files
by switching to `asyncio.run()`.

Legend: ✅ verified · ⚠️ partially verified / known gap · ❌ not verified

---

## Conversation-first chat core

- ✅ Unit + integration tested: thread creation, history persistence
  (`conversation_state`), WS streaming (`chat_messages/tests.py`).
- ✅ Live-verified: real multi-turn conversations over the WebSocket against
  the running dev server, across every provider (see below).
- ✅ Fixed this session: `Thread.updated_at` was never actually persisted past
  creation (`update_fields` omitted it despite `auto_now=True`) — conversation
  list ordering by "most recently used" silently never worked. Confirmed via a
  direct DB check before fixing, regression test added.
- ⚠️ Consumer error handling: a generic `except Exception` now catches
  unexpected failures and returns a clean WS error frame instead of dropping
  the connection — verified via a real 429 from Gemini during live testing,
  and via a mocked regression test. No broader chaos/fault-injection testing
  done beyond the cases actually hit.

## BYOK + credit system

- ✅ Unit tested: `_compute_cost_credits`, `deduct_credits`, credit-exhaustion
  gate (`InsufficientCreditsError` before any provider call).
- ✅ Live-verified: real Anthropic call with the shared key deducted real
  credits; BYOK key bypasses metering as designed.
- ⚠️ Pricing tables (`PRICING` dict per provider) are hand-entered from each
  provider's published pricing page at the time they were added, cross-checked
  against the installed SDK's actual response `usage` fields — not
  programmatically kept in sync. **Provider pricing changes over time; nobody
  will get paged when a table goes stale.**
- ❌ No test for concurrent requests racing on `credits_remaining` (no
  row-level locking on the decrement) — a user hammering the send button in
  parallel across tabs could theoretically go negative. Not observed in
  practice; not guarded against either.

## Multi-provider (Anthropic / OpenAI / Mistral / Gemini)

- ✅ Unit tested per provider: request-shape translation, tool-call
  extraction, streaming accumulation, usage capture (`ai_providers/tests/`).
- ✅ Live-verified end-to-end with real API calls:
  - **Anthropic**: chat + tool use, confirmed early and repeatedly.
  - **Gemini**: chat, tool use, image generation (`gemini-2.5-flash-image`),
    delegation target — all confirmed live, including chasing down and fixing
    a real `thought_signature` bug (see Gemini-specific note below).
  - **OpenAI**: chat confirmed live after the user topped up billing; image
    generation (`gpt-image-2`) confirmed live.
  - **Mistral**: chat confirmed live once, early in the session
    (`mistral-large-latest`, plain text turn). **Never live-tested with tool
    use or streaming against the real API** — those paths are unit-tested
    with mocks only.
- ⚠️ Model ID drift risk: model IDs (e.g. `gpt-image-2`, `gemini-2.5-flash-image`)
  were correct as of when they were added, verified against official docs and
  the installed SDK — providers rename/deprecate models on their own schedule
  and nothing here detects that automatically.

### Gemini `thought_signature` bug (fixed, worth knowing about)

Gemini's "thinking" models attach an opaque `thought_signature` to each
function-call `Part`, which must be echoed back verbatim on the next turn.
Two related bugs were found and fixed via a live failure, not speculatively:

1. The code only ever read the convenience `.function_calls` accessor, which
   silently drops the sibling `thought_signature` field — fixed by walking
   `candidates[0].content.parts` directly.
2. Synthetic call IDs (Gemini's SDK never returns a real `FunctionCall.id`)
   were generated from a per-response local index, so two separate rounds of
   calling the *same* tool in one conversation collided on the same ID,
   silently corrupting the stored signature. Fixed with a monotonic
   per-instance counter. Regression test simulates exactly this collision.

If Gemini tool-calling breaks again with a `thought_signature` error, this is
the first place to look — and check whether it's a *third* round-trip
scenario not covered by the current fix.

## Projects

- ✅ Unit + integration tested: CRUD, `SET_NULL` on delete (conversations
  detach, aren't deleted), thread_count annotation.
- ✅ Live-verified: create/rename/delete via the running API and UI.

## MCP tool integration (per-project)

- ✅ Unit tested: CRUD API, project-scoping, transport validation (stdio
  requires `command`, sse requires `url`), tool-fetch failure isolation
  (one unreachable server doesn't break others).
- ✅ Live-verified: real stdio MCP server (`python manage.py run_mcp_server`,
  this project's own tool server) added via the UI, tools listed and called
  successfully from a real conversation.
- ❌ SSE transport has never been exercised against a real SSE MCP server —
  only unit-tested with mocks. stdio is the only transport confirmed live.

## Image generation (`generate_image` tool)

- ✅ Unit tested: both providers' request/response shapes, credit deduction,
  storage service (`image_providers/tests/`).
- ✅ Live-verified: real images generated and displayed via both OpenAI
  (`gpt-image-2`) and Gemini (`gemini-2.5-flash-image`), persisted to local
  disk storage and rendered via Markdown in the chat.
- ⚠️ **Storage is local disk (`MEDIA_ROOT`), not object storage.** Fine for
  single-instance dev; will not survive a redeploy on most PaaS targets
  (Railway/Render use ephemeral filesystems unless a volume is attached).
  This needs to become S3-compatible storage before real deployment — not
  done, not started.
- ❌ No cleanup/retention policy for generated images — they accumulate
  forever on disk.

## Model delegation (`delegate_to_model`)

- ✅ Unit tested: confirmation gate (declines return a message to the model
  instead of dispatching), model-ID fallback to a provider's default when the
  delegating model guesses an invalid one, credit deduction on the delegated
  call, no-recursion (`allow_delegation=False` on the sub-call).
- ✅ Live-verified end-to-end: real pause-for-confirmation over the
  WebSocket (background task + `asyncio.Future`, since Channels dispatches
  one message at a time per connection), user confirms in the UI, delegated
  call actually dispatches and its response folds back into the original
  conversation.
- ⚠️ The pause/resume mechanism is bespoke (not a Channels-native pattern) —
  works, covered by tests including a disconnect-while-pending case, but it's
  the newest and most structurally unusual piece of the WS layer. Worth a
  second pair of eyes if anyone touches `consumers.py` later.

## Admin / user management

- ✅ Unit tested: permission boundary (`IsAdminUser`/`is_staff`) on every
  action, credit validation (rejects negative), `is_staff` not writable
  through this endpoint (no self-escalation path).
- ✅ Live-verified: real admin login, listed users, edited a real credit
  balance through the API.
- Deliberately out of scope: no delete, no role changes (promote/demote
  admin) — not asked for, and higher-risk than what was.

## Long-term memory (librarian)

- ✅ Unit tested: extraction logic, storage.
- ⚠️ A real bug (`SynchronousOnlyOperation` from a sync ORM call inside async
  code) meant memories were never actually persisting for an unknown period
  before this was caught and fixed earlier in the project's life — mentioned
  here because it's the kind of silent failure this whole review process is
  meant to catch earlier next time.
- ❌ No deduplication — restating the same fact across turns creates
  duplicate rows. Known, not fixed, not blocking.

## Infrastructure / CI

- ✅ **New this pass**: `.github/workflows/ci.yml` — Postgres (`pgvector/pgvector:pg15`)
  + Redis service containers, migrations, `manage.py check`, full `pytest`
  run. Verified locally that every step it runs actually passes before
  relying on it.
- ❌ Not deployed anywhere yet. Deployment architecture is decided (Vercel +
  Railway/Render) but not executed — biggest remaining gap before a real MVP
  launch, along with the image-storage issue above.
- ❌ No Sentry/error tracking, no CSP/HSTS headers.

## Explicitly out of scope this pass (not gaps, just not asked for)

- Files-per-project.
- i18n string extraction (scaffolding kept, not filled in — deliberate).
- React error boundaries.
