"""Textual app: minimal ACP TUI.

Layout:
  ┌─ acp-tui ─────────────────────────────────┐
  │ status: connected · sessionId=…           │  ← Static
  │ ┌───────────────────────────────────────┐ │
  │ │ message log                           │ │  ← RichLog (scrollable)
  │ │ ...                                   │ │
  │ └───────────────────────────────────────┘ │
  │ > prompt                                  │  ← Input
  └───────────────────────────────────────────┘

Lifecycle on mount:
  1. Connect WebSocket
  2. Send `initialize`
  3. Send `session/new` → obtain the ACP-side sessionId
  4. Enable the prompt input

Each input submission sends `session/prompt`. The input is disabled while
the turn is in flight so the user can't queue overlapping prompts (which
the bridge would reject anyway, but disabling it here keeps the UI honest).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog, Static

from .acp_client import ACPClient, ACPError

logger = logging.getLogger(__name__)


class ACPApp(App):
    TITLE = "acp-tui"
    BINDINGS = [Binding("ctrl+c", "quit", "Quit", priority=True)]

    CSS = """
    #status {
        dock: top;
        height: 1;
        padding: 0 1;
        background: $primary-darken-2;
        color: $text;
    }
    #log {
        background: $surface;
        padding: 0 1;
    }
    Input {
        dock: bottom;
    }
    """

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url
        self.client: ACPClient | None = None
        self.acp_session_id: str | None = None
        self._run_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("connecting…", id="status")
        yield RichLog(highlight=True, markup=True, id="log", wrap=True)
        yield Input(
            placeholder="(waiting for connection)",
            id="prompt-input",
            disabled=True,
        )
        yield Footer()

    async def on_mount(self) -> None:
        self._run_task = asyncio.create_task(self._run(), name="acp-app-run")

    async def on_unmount(self) -> None:
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()
        if self.client is not None:
            await self.client.close()

    async def _run(self) -> None:
        log = self.query_one("#log", RichLog)
        status = self.query_one("#status", Static)
        prompt_input = self.query_one("#prompt-input", Input)

        try:
            self.client = ACPClient(self.url)
            log.write(f"[dim]connecting to {self.url}[/]")
            await self.client.connect()

            log.write("[dim]→ initialize[/]")
            init = await self.client.request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientInfo": {"name": "acp-tui", "version": "0.1.0"},
                    "clientCapabilities": {},
                },
            )
            agent_info = (init or {}).get("agentInfo", {})
            log.write(
                f"[dim]← initialize: {agent_info.get('name', '?')} v{agent_info.get('version', '?')}[/]"
            )

            log.write("[dim]→ session/new[/]")
            new = await self.client.request(
                "session/new",
                {"cwd": str(Path.cwd()), "mcpServers": []},
            )
            self.acp_session_id = (new or {}).get("sessionId")
            log.write(f"[dim]← session/new: sessionId={self.acp_session_id}[/]")

            status.update(f"connected · sessionId={self.acp_session_id}")
            prompt_input.placeholder = "type a prompt and press enter"
            prompt_input.disabled = False
            prompt_input.focus()

            async for msg in self.client.incoming():
                self._render_incoming(msg)

            log.write("[dim]connection closed by peer[/]")
            status.update("disconnected")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("app run loop crashed")
            log.write(f"[red]error:[/] {type(exc).__name__}: {exc}")
            status.update(f"error: {type(exc).__name__}: {exc}")

    def _render_incoming(self, msg: dict) -> None:
        log = self.query_one("#log", RichLog)
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params", {})
        params_blob = json.dumps(params)
        if len(params_blob) > 220:
            params_blob = params_blob[:220] + "…"

        if method and msg_id is None:
            if method == "session/update":
                update = params.get("update", {}) if isinstance(params, dict) else {}
                kind = update.get("sessionUpdate", "?")
                log.write(f"[cyan]update[/] {kind}: [dim]{json.dumps(update)[:220]}[/]")
            else:
                log.write(f"[yellow]notify[/] {method} [dim]{params_blob}[/]")
        elif method and msg_id is not None:
            log.write(
                f"[magenta]agent-request[/] id={msg_id} {method} [dim]{params_blob}[/]"
            )
            log.write("  [red dim](not handled in v0 — agent may stall if it expects a response)[/]")
        else:
            log.write(f"[dim]{json.dumps(msg)[:220]}[/]")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.client is None or self.acp_session_id is None:
            return
        text = event.value.strip()
        if not text:
            return

        log = self.query_one("#log", RichLog)
        prompt_input = self.query_one("#prompt-input", Input)

        log.write(f"[bold green]you ▶[/] {text}")
        prompt_input.value = ""
        prompt_input.disabled = True
        prompt_input.placeholder = "agent is processing…"

        try:
            result = await self.client.request(
                "session/prompt",
                {
                    "sessionId": self.acp_session_id,
                    "prompt": [{"type": "text", "text": text}],
                },
            )
            stop_reason = result.get("stopReason") if isinstance(result, dict) else None
            log.write(f"[bold green]turn complete[/] [dim](stopReason={stop_reason})[/]")
        except ACPError as exc:
            log.write(f"[red]prompt error[/] code={exc.code} {exc.message}")
        except Exception as exc:
            log.write(f"[red]send error[/] {type(exc).__name__}: {exc}")
        finally:
            prompt_input.disabled = False
            prompt_input.placeholder = "type a prompt and press enter"
            prompt_input.focus()
