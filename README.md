# Commish Bot

A self-hosted assistant for dynasty fantasy league iMessage group chats. Mention it with `@commish` and it answers rules and policy questions. It only answers when it can cite a specific league source: a doc section, an approved vote screenshot, a commissioner ruling, or a Sleeper league setting. Anything else gets:

> I can't confirm this from league records. Flagging for the commish.

and the question goes privately to the commissioner.

- **Plan:** [`docs/PLAN.md`](docs/PLAN.md) covers architecture, the phases with exit criteria, the eval gate, risks and open questions.
- **Current phase:** 0, the iMessage "pong" spike.

## Phase 0 (current)

The bot runs on an always-on Mac:
- **Messages.app** is signed into a dedicated bot Apple Account.
- **BlueBubbles Server** delivers each new message to this app over a localhost webhook.
- The app replies `pong` to any `@commish` message in the whitelisted group chat.

Set it up with [`deploy/macos-setup.md`](deploy/macos-setup.md).

```bash
uv sync
uv run pytest                      # unit tests (no Mac needed)
uv run commish bb-ping             # BlueBubbles reachable?
uv run commish chats               # find the group chat GUID
uv run commish serve               # webhook server on 127.0.0.1:8787
uv run commish spike-report        # Phase 0 exit-criteria summary from logs/events.jsonl
```

### Configuration

- **Secrets** live in the macOS Keychain (`keyring set commish <name>`), under two names: `bluebubbles_password` and `webhook_token`. For tests, a `COMMISH_<NAME>` environment variable overrides the Keychain.
- **Other settings** are `COMMISH_*` environment variables, set in the launchd plist:

| Variable | Default | Meaning |
|---|---|---|
| `COMMISH_BLUEBUBBLES_URL` | `http://127.0.0.1:1234` | BlueBubbles server |
| `COMMISH_SEND_METHOD` | `apple-script` | `private-api` once SIP is off and the helper is installed |
| `COMMISH_ALLOWED_CHAT_GUIDS` | *(empty: answers nowhere)* | Comma-separated chat GUIDs |
| `COMMISH_MAX_PER_SENDER_PER_10MIN` | `20` | Per-sender question limit |
| `COMMISH_MAX_OUTBOUND_PER_DAY` | `100` | Hard cap on bot messages per day (protects the Apple ID) |
| `COMMISH_LOG_DIR` | `logs` | Where `events.jsonl` is written |

### Privacy

Messages that don't mention `@commish` are logged only as a GUID plus the reason they were ignored. Their text is never stored, and nothing is sent to any API.
