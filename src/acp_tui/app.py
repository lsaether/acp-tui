"""Textual app: minimal ACP TUI.

Layout:
  ┌─ acp-tui ─────────────────────────────────┐
  │ status: connected · sessionId=…           │  ← Static
  │ ┌───────────────────────────────────────┐ │
  │ │ message log                           │ │  ← RichLog (scrollable)
  │ │ ...                                   │ │
  │ └───────────────────────────────────────┘ │
  │ > prompt                                  │  ← PromptInput
  └───────────────────────────────────────────┘

Lifecycle on mount:
  1. Connect WebSocket
  2. Send `initialize`
  3. Send `session/new` → obtain the ACP-side sessionId
  4. Enable the prompt input

Each input submission sends `session/prompt`. The input is disabled while
the turn is in flight so the user can't queue overlapping prompts (which
the bridge would reject anyway, but disabling it here keeps the UI honest).

When the agent sends a `session/request_permission` request, the TUI
renders it inline in the log with a key legend (`[1] Allow once  [2]
Deny  [esc] cancel`). Number keys are only consumed by the permission
flow when the input box is empty, so typing in the prompt isn't
hijacked. Esc always cancels the active permission while one is
pending. Stacked permissions queue FIFO; only the front of the queue
responds to keys.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog, Static

from .acp_client import ACPClient, ACPError

logger = logging.getLogger(__name__)


def _format_outcome(result: Any, error: Any) -> str:
    """Render a short label for an agent-initiated request resolution.

    Covers the only shape that flows through this path today —
    `session/request_permission` returning `{outcome: {...}}` — and
    falls back to a compact JSON dump for anything else.
    """
    if isinstance(result, dict):
        outcome = result.get("outcome")
        if isinstance(outcome, dict):
            kind = outcome.get("outcome")
            if kind == "selected":
                opt = outcome.get("optionId", "?")
                return f"selected {opt}"
            if kind == "cancelled":
                return "cancelled"
        return json.dumps(result)[:120]
    if isinstance(error, dict):
        code = error.get("code", "?")
        message = error.get("message", "")
        return f"error {code}: {message}"
    return "(no payload)"


@dataclass
class PendingPermission:
    """One unresolved `session/request_permission` from the agent."""

    seq: int
    request_id: Any
    options: list[dict[str, Any]] = field(default_factory=list)
    tool_summary: str = ""

    def key_legend(self) -> str:
        parts = []
        for idx, opt in enumerate(self.options[:9], start=1):
            name = opt.get("name") or opt.get("optionId") or f"option {idx}"
            parts.append(f"[{idx}] {name}")
        parts.append("[esc] cancel")
        return "  ".join(parts)


class PromptInput(Input):
    """Input subclass that lets the parent app intercept permission keys.

    Digit keys (1-9) are forwarded to the app's permission handler only
    when the input value is empty — otherwise they pass through as normal
    text. Esc always cancels the active permission while one is pending.
    Other keys fall through to `Input`'s default editing behavior.
    """

    async def on_key(self, event: events.Key) -> None:
        app = self.app
        if not isinstance(app, ACPApp) or not app.has_pending_permission():
            return
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            await app.permission_cancel()
            return
        if event.key in "123456789" and not self.value:
            event.stop()
            event.prevent_default()
            await app.permission_select(int(event.key))


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
        self._pending_permissions: deque[PendingPermission] = deque()
        self._next_permission_seq = 1

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("connecting…", id="status")
        yield RichLog(highlight=True, markup=True, id="log", wrap=True)
        yield PromptInput(
            placeholder="(waiting for connection)",
            id="prompt-input",
            disabled=True,
        )
        yield Footer()

    def has_pending_permission(self) -> bool:
        return bool(self._pending_permissions)

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
        params = msg.get("params", {}) if isinstance(msg.get("params"), dict) else {}
        params_blob = json.dumps(params)
        if len(params_blob) > 220:
            params_blob = params_blob[:220] + "…"

        if method and msg_id is None:
            if method == "session/update":
                update = params.get("update", {}) if isinstance(params, dict) else {}
                kind = update.get("sessionUpdate", "?")
                log.write(f"[cyan]update[/] {kind}: [dim]{json.dumps(update)[:220]}[/]")
            elif method == "amux/agent_request_resolved":
                self._handle_agent_request_resolved(params)
            else:
                log.write(f"[yellow]notify[/] {method} [dim]{params_blob}[/]")
        elif method and msg_id is not None:
            if method == "session/request_permission":
                self._enqueue_permission(msg_id, params)
            else:
                log.write(
                    f"[magenta]agent-request[/] id={msg_id} {method} [dim]{params_blob}[/]"
                )
                log.write(
                    "  [red dim](not handled — agent may stall if it expects a response)[/]"
                )
        else:
            log.write(f"[dim]{json.dumps(msg)[:220]}[/]")

    def _enqueue_permission(self, request_id: Any, params: dict[str, Any]) -> None:
        log = self.query_one("#log", RichLog)
        options = params.get("options")
        if not isinstance(options, list):
            options = []
        tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        title = tool_call.get("title") or tool_call.get("toolCallId") or "(no tool)"
        kind = tool_call.get("kind") or "?"
        tool_summary = f"{kind} · {title}"

        pending = PendingPermission(
            seq=self._next_permission_seq,
            request_id=request_id,
            options=options,
            tool_summary=tool_summary,
        )
        self._next_permission_seq += 1
        was_empty = not self._pending_permissions
        self._pending_permissions.append(pending)

        tag = "[bold magenta]permission[/]" if was_empty else "[bold magenta]permission queued[/]"
        log.write(f"{tag} #{pending.seq} (id={request_id}) {pending.tool_summary}")
        if not options:
            log.write(
                "  [red dim]no options provided — pressing any key sends cancelled[/]"
            )
        else:
            log.write(f"  {pending.key_legend()}")
        if not was_empty:
            log.write(
                f"  [dim](resolve permission #{self._pending_permissions[0].seq} first)[/]"
            )

    def _handle_agent_request_resolved(self, params: dict[str, Any]) -> None:
        """An agent-initiated request was resolved on another peer, by us,
        or implicitly by the agent abandoning it at turn end. Dismiss the
        matching pending entry if it's still in our queue."""
        log = self.query_one("#log", RichLog)
        request_id = params.get("requestId")
        resolved_by = params.get("resolvedBy", "?")
        result = params.get("result")
        error = params.get("error")

        is_turn_end = resolved_by == "mux:turn-ended"
        if is_turn_end:
            outcome_label = "expired (no reply before turn ended)"
        else:
            outcome_label = _format_outcome(result, error)

        match = next(
            (p for p in self._pending_permissions if p.request_id == request_id),
            None,
        )
        if match is None:
            # Either we resolved this locally (already logged) or this is
            # a turn-end cleanup for a request we never tracked. Keep a
            # one-line audit so the user sees the mux acknowledgment.
            log.write(
                f"  [dim]resolved (mux ack): id={request_id} by {resolved_by} → {outcome_label}[/]"
            )
            return
        try:
            self._pending_permissions.remove(match)
        except ValueError:
            pass
        if is_turn_end:
            log.write(
                f"  [bold yellow]permission #{match.seq} expired[/] → {outcome_label}"
            )
        else:
            log.write(
                f"  [bold magenta]permission #{match.seq} resolved by {resolved_by}[/] → {outcome_label}"
            )
        if self._pending_permissions:
            nxt = self._pending_permissions[0]
            log.write(f"  [dim]next active: permission #{nxt.seq}[/]")

    async def permission_select(self, index: int) -> None:
        """Reply to the active permission with the option at 1-based `index`."""
        if not self._pending_permissions or self.client is None:
            return
        pending = self._pending_permissions[0]
        if not pending.options:
            await self._respond_cancelled(pending)
            return
        if index < 1 or index > len(pending.options):
            log = self.query_one("#log", RichLog)
            log.write(
                f"  [red]ignored:[/] permission #{pending.seq} has no option [{index}]"
            )
            return
        choice = pending.options[index - 1]
        option_id = choice.get("optionId")
        outcome = {"outcome": "selected"}
        if option_id is not None:
            outcome["optionId"] = option_id
        await self._respond(pending, {"outcome": outcome}, label=f"selected {option_id}")

    async def permission_cancel(self) -> None:
        if not self._pending_permissions:
            return
        await self._respond_cancelled(self._pending_permissions[0])

    async def _respond_cancelled(self, pending: PendingPermission) -> None:
        await self._respond(pending, {"outcome": {"outcome": "cancelled"}}, label="cancelled")

    async def _respond(
        self, pending: PendingPermission, result: dict[str, Any], *, label: str
    ) -> None:
        log = self.query_one("#log", RichLog)
        assert self.client is not None
        try:
            await self.client.respond(pending.request_id, result)
        except Exception as exc:
            log.write(
                f"  [red]permission #{pending.seq} respond failed:[/] {type(exc).__name__}: {exc}"
            )
            return
        log.write(f"  [bold magenta]permission #{pending.seq} →[/] {label}")
        # Pop the resolved permission; reveal the next one if any.
        if self._pending_permissions and self._pending_permissions[0] is pending:
            self._pending_permissions.popleft()
        else:
            try:
                self._pending_permissions.remove(pending)
            except ValueError:
                pass
        if self._pending_permissions:
            nxt = self._pending_permissions[0]
            log.write(f"  [dim]next active: permission #{nxt.seq}[/]")

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
