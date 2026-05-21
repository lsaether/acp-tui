# acp-tui

> Minimal debugger TUI for the Agent Client Protocol ([ACP](https://agentclientprotocol.com)). Connects to any ACP-over-WebSocket server and displays raw protocol frames. Compatible with [acp-mux](https://github.com/lsaether/acp-mux) for multi-subscriber sessions.

A truly minimal v0: connect, see every frame, send prompts. No filtering, no special-casing of any agent or multiplex layer — what the server sent is what you see. Useful for verifying ACP servers, debugging multiplex layers, and validating end-to-end correctness of new ACP clients.

## Install

```bash
cd ~/Code/acp-tui
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run

Point it at any ACP-over-WebSocket server:

```bash
acp-tui --url ws://127.0.0.1:8765/acp --session my-debug
```

On startup it sends `initialize`, creates a new ACP session via `session/new`, and shows the resulting `sessionId` in the header. Type into the input box and press Enter to send a `session/prompt`. Every incoming frame — token deltas, tool events, agent-initiated requests, multiplex notifications — renders in the log as it arrives.

### Responding to `session/request_permission`

When the agent asks for permission to run a tool, the TUI renders the request inline with a numbered key legend, e.g.

```
permission #1 (id=10001) execute · demo_tool
  [1] Allow once  [2] Deny  [esc] cancel
```

Press the matching digit to select an option, or `esc` to send a cancelled outcome. Digit keys are only intercepted when the input box is empty, so typing a prompt that happens to contain a digit isn't hijacked. If several permissions arrive while one is unresolved, they queue FIFO — only the front responds to keys until you resolve it.

When connected via acp-mux (multi-subscriber), the mux broadcasts `amux/agent_request_resolved` to every peer as soon as one peer answers. The other TUIs dismiss their copy of the pending permission and log a one-liner showing who resolved it and which option was selected, so no client is left staring at a stuck prompt.

If no peer answers and the agent's own deadline fires (e.g. hermes defaults to denying a permission request after 60s and carries on), the mux notices the turn ending with the request still outstanding and broadcasts a `resolvedBy: "mux:turn-ended"` cleanup. The TUI dismisses the prompt with an `expired (no reply before turn ended)` line so you can see exactly why the entry went away.

### Debugging a stdio ACP agent (websocat bridge)

The canonical ACP transport is stdio — agents like `hermes acp`, `claude-code-acp`, and others speak JSON-RPC over stdin/stdout. To point acp-tui at a stdio agent, wrap it in a one-shot WebSocket listener with [websocat](https://github.com/vi/websocat):

```bash
# Terminal A: spawn the agent and expose its stdio over WS
websocat --text ws-l:127.0.0.1:8765 sh-c:"hermes acp"

# Terminal B: connect with acp-tui
acp-tui --url ws://127.0.0.1:8765/ --session debug
```

websocat spawns a fresh agent subprocess per WebSocket connection — the right behavior for 1:1 ACP debugging. Each acp-tui run gets its own clean agent process. For multi-subscriber sessions sharing one agent process, use acp-mux below.

### Multi-subscriber example (acp-mux)

[acp-mux](https://github.com/lsaether/acp-mux) lets multiple clients attach to one ACP agent session. Launch one acp-tui per peer, sharing the same session id:

```bash
# Terminal A
acp-tui --url ws://127.0.0.1:9876/acp --session shared --peer-id alice --peer-name Alice

# Terminal B
acp-tui --url ws://127.0.0.1:9876/acp --session shared --peer-id bob --peer-name Bob
```

Both subscribers see the agent's `session/update` stream; either can send a `session/prompt`. `amux/*` notifications (peer presence, turn bookends, busy state) render in the log alongside the ACP stream — same generic `notify` treatment as any other notification method, no special-casing.

## CLI options

| Flag | Default | Purpose |
|---|---|---|
| `--url` | `ws://127.0.0.1:8765/acp` | ACP server WebSocket URL. If the URL has no query string, the flags below are encoded into it; if it does, the URL is trusted verbatim. |
| `--session` | random | Session id (`?session=<id>`). Pick a stable id if you want to reattach within the server's TTL grace period. |
| `--peer-id` | random | Per-subscriber identity (`?peer_id=<id>`). Required by `acp-mux` (it closes the WS with code 4400 if absent); ignored by ACP servers that don't model per-subscriber identity. |
| `--peer-name` | _(none)_ | Optional human-friendly name for this subscriber (`acp-mux` surfaces it in `amux/peer_joined` events). |
| `--role` | _(none)_ | Optional role tag for this subscriber. |
| `--log-level` | `warning` | `debug` / `info` / `warning` / `error` |

## Status

v0. One session, one connection, one input box. No history scroll, no markdown rendering, no copy/search. Intentionally unopinionated — its job is to show the raw protocol, not to be a polished daily-driver client.
