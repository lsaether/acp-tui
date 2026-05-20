"""CLI entry: `acp-tui [--url ...] [--session ...] [--log-level ...]`."""

from __future__ import annotations

import argparse
import logging
import secrets
import sys
from urllib.parse import urlencode, urlparse, urlunparse

from .app import ACPApp


def _ensure_session_param(url: str, session: str) -> str:
    """Return `url` with `?session=<session>` appended if no query string is present."""
    parsed = urlparse(url)
    if parsed.query:
        # Caller put their own query — trust it.
        return url
    return urlunparse(parsed._replace(query=urlencode({"session": session})))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="acp-tui",
        description="Minimal terminal client for an ACP server (e.g. hermes-bridge).",
    )
    p.add_argument(
        "--url",
        default="ws://127.0.0.1:8765/acp",
        help="Bridge WebSocket URL. If it has no query string, --session is appended.",
    )
    p.add_argument(
        "--session",
        default=None,
        help="Bridge session id. Defaults to a random short token (no reattach).",
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
    url = _ensure_session_param(args.url, session)

    app = ACPApp(url=url)
    app.run()


if __name__ == "__main__":
    main()
