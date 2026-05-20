# acp-tui

> Minimal terminal client for any Agent Client Protocol ([ACP](https://agentclientprotocol.com)) server. Connects over WebSocket — pairs with [hermes-bridge](https://github.com/lsaether/hermes-bridge) for Hermes sessions.

A truly minimal v0: connect to a session, see the stream, send prompts. No session picker, no markdown rendering, no themes. The smallest useful unit.

## Install

```bash
cd ~/Code/acp-tui
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run

First, run [hermes-bridge](https://github.com/lsaether/hermes-bridge) somewhere:

```bash
hermes-bridge --port 8765 --hermes-acp-cmd /path/to/hermes-acp
```

Then point acp-tui at it:

```bash
acp-tui --url ws://127.0.0.1:8765/acp --session my-desktop
```

On startup it sends `initialize`, creates a new ACP session via `session/new`, and shows the resulting `sessionId` in the header. Type into the input box and press Enter to send a `session/prompt`. Notifications (token deltas, tool events) stream into the log as they arrive.

## CLI options

| Flag | Default | Purpose |
|---|---|---|
| `--url` | `ws://127.0.0.1:8765/acp` | Bridge WebSocket URL (no `?session` — that's set by `--session`) |
| `--session` | random | Bridge session id (`?session=<id>`). Pick a stable id if you want to reattach within the bridge's TTL grace period. |
| `--log-level` | `info` | `debug` / `info` / `warning` / `error` |

## Status

v0. One session, one connection, one input box. No history scroll, no markdown, no copy/search. See if the loop is worth investing in before adding polish.
