# Commish Bot

A citation-only rules assistant for dynasty fantasy league iMessage group chats. The full design, phase plan and open questions are in `docs/PLAN.md`; read it before any non-trivial change.

## Commands

```bash
uv sync                      # install (Python 3.12, pinned in .python-version)
uv run pytest -q             # all tests, including e2e over real HTTP with a fake BlueBubbles
uv run ruff check . && uv run ruff format --check .
uv run mypy                  # strict, on commish/
```

CI (`.github/workflows/ci.yml`) runs all of the above on every PR. Keep it green.

## Invariants (never trade these away)

- **Correctness over helpfulness.**
  - An answer ships only with a citation to a retrieved, approved source.
  - Anything uncertain, conflicting, possibly superseded, or a judgment call abstains and gets flagged to the commissioner.
  - Any error path (LLM failure, quota, bad JSON, timeout) must abstain, never guess.
- **Deterministic checks can only reject, never approve.** The LLM verifier is an additional gate, not a replacement for code checks.
- **League isolation.** One SQLite database per league. A pipeline instance serves exactly one league. No shared caches.
- **Privacy.**
  - Messages that don't mention `@commish` are never stored (GUID + ignore reason only) and never sent to an LLM.
  - Secrets come from the macOS Keychain via `commish.secrets.get_secret` (a `COMMISH_<NAME>` env var overrides it for tests). Never put secrets in files.
- **Loop safety.** Intake rejects the bot's own messages before anything else.
- **Apple ID safety.** The outbound daily cap in `ratelimit.py` stays on.
- **Transport-neutral core.** Only `commish/transport/*` may know about BlueBubbles. Everything else uses `InboundMessage` / `Transport`.
- **Free services only in v1.** The LLM is the Gemini API free tier behind a provider interface. Don't add paid dependencies without asking.

## Deployment facts

- **The bot Mac must stay on macOS 15 Sequoia.** AppleScript sends into group chats are broken on macOS 26 Tahoe.
- **BlueBubbles payload shapes** follow Server v1.9.x. See `tests/fixtures.py` and the notes in `commish/transport/bluebubbles.py`.
