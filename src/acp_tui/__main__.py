"""CLI entry: `acp-tui [--url ...] [--session ...] [--peer-id ...] [--peer-name ...] [--log-level ...]`."""

from __future__ import annotations

import argparse
import logging
import secrets
import sys
from urllib.parse import urlencode, urlparse, urlunparse

from .app import ACPApp


def _ensure_query_params(
    url: str,
    session: str,
    peer_id: str,
    peer_name: str | None,
    role: str | None,
) -> str:
    """Return `url` with the standard ACP-server query params attached.

    If the caller already put a query string on `url`, trust it verbatim —
    we don't want to second-guess explicit configuration. Otherwise build
    `?session=...&peer_id=...[&peer_name=...][&role=...]` from CLI args.
    The `peer_id` param is required by acp-mux (close 4400 without it) and
    ignored by ACP servers that don't model per-subscriber identity, so
    it's safe to always include.
    """
    parsed = urlparse(url)
    if parsed.query:
        return url
    params: dict[str, str] = {"session": session, "peer_id": peer_id}
    if peer_name:
        params["peer_name"] = peer_name
    if role:
        params["role"] = role
    return urlunparse(parsed._replace(query=urlencode(params)))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="acp-tui",
        description=(
            "Minimal debugger TUI for the Agent Client Protocol (ACP). "
            "Displays raw protocol frames from any ACP-over-WebSocket "
            "server. Compatible with acp-mux for multi-subscriber sessions."
        ),
    )
    p.add_argument(
        "--url",
        default="ws://127.0.0.1:8765/acp",
        help="ACP server WebSocket URL. If it has no query string, "
        "--session, --peer-id, --peer-name, --role are encoded into it.",
    )
    p.add_argument(
        "--session",
        default=None,
        help="Session id. Defaults to a random short token (no reattach).",
    )
    p.add_argument(
        "--peer-id",
        default=None,
        help="Per-subscriber identity. Required by amux; ignored by other "
        "ACP servers. Defaults to a random short token.",
    )
    p.add_argument(
        "--peer-name",
        default=None,
        help="Optional human-friendly name for this subscriber (amux only).",
    )
    p.add_argument(
        "--role",
        default=None,
        help="Optional role tag for this subscriber (amux only).",
    )
    p.add_argument(
        "--log-level",
        default="warning",
        choices=("debug", "info", "warning", "error"),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    session = args.session or f"acp-tui-{secrets.token_hex(4)}"
    peer_id = args.peer_id or f"tui-{secrets.token_hex(3)}"
    url = _ensure_query_params(args.url, session, peer_id, args.peer_name, args.role)

    app = ACPApp(url=url)
    app.run()


if __name__ == "__main__":
    main()
