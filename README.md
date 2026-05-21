# acp-tui

> Minimal terminal client for any Agent Client Protocol ([ACP](https://agentclientprotocol.com)) server. Connects over WebSocket — pairs with [hermes-bridge](https://github.com/lsaether/hermes-bridge) for 1:1 Hermes sessions, or [acp-mux](https://github.com/lsaether/acp-mux) for multi-subscriber sessions.

A truly minimal v0: connect to a session, see the stream, send prompts. No session picker, no markdown rendering, no themes. The smallest useful unit. Because it renders every incoming frame in the log without filtering, it doubles nicely as a protocol-level debug surface.

## Install

```bash
cd ~/Code/acp-tui
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run

### Against hermes-bridge (1:1 sessions)

First, run [hermes-bridge](https://github.com/lsaether/hermes-bridge) somewhere:

```bash
hermes-bridge --port 8765 --hermes-acp-cmd /path/to/hermes-acp
```

Then point acp-tui at it:

```bash
acp-tui --url ws://127.0.0.1:8765/acp --session my-desktop
```

### Against acp-mux (multi-subscriber sessions)

[acp-mux](https://github.com/lsaether/acp-mux) lets multiple clients attach to one ACP agent session. Spin up `amux` against any ACP agent (e.g. `hermes acp`):

```bash
amux --port 9876 --agent-cmd 'hermes acp'
```

Then launch one acp-tui per peer, sharing the same session id:

```bash
# Terminal A
acp-tui --url ws://127.0.0.1:9876/acp --session shared --peer-id alice --peer-name Alice

# Terminal B
acp-tui --url ws://127.0.0.1:9876/acp --session shared --peer-id bob --peer-name Bob
```

Both subscribers see the agent's `session/update` stream; either can send a `session/prompt`. `amux/*` notifications (peer presence, turn bookends, busy state) render in the log alongside the ACP stream.

### Behavior

On startup it sends `initialize`, creates a new ACP session via `session/new`, and shows the resulting `sessionId` in the header. Type into the input box and press Enter to send a `session/prompt`. Notifications (token deltas, tool events, multiplex events) stream into the log as they arrive.

## CLI options

| Flag | Default | Purpose |
|---|---|---|
| `--url` | `ws://127.0.0.1:8765/acp` | ACP server WebSocket URL. If the URL has no query string, the flags below are encoded into it; if it does, the URL is trusted verbatim. |
| `--session` | random | Session id (`?session=<id>`). Pick a stable id if you want to reattach within the server's TTL grace period. |
| `--peer-id` | random | Per-subscriber identity (`?peer_id=<id>`). Required by `amux` (it closes the WS with code 4400 if absent); ignored by other ACP servers. |
| `--peer-name` | _(none)_ | Optional human-friendly name for this subscriber (`amux` surfaces it in `amux/peer_joined` events; other servers ignore it). |
| `--role` | _(none)_ | Optional role tag for this subscriber (`amux` only). |
| `--log-level` | `warning` | `debug` / `info` / `warning` / `error` |

## Status

v0. One session, one connection, one input box. No history scroll, no markdown, no copy/search. See if the loop is worth investing in before adding polish.
