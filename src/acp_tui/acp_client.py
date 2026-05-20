"""Async ACP client over WebSocket.

Wraps a `websockets` connection and exposes a small JSON-RPC API:

  - `request(method, params)` sends a request, awaits the matching response,
    raises ACPError if the agent returned an error.
  - `notify(method, params)` fires a JSON-RPC notification (no id).
  - `incoming()` is an async iterator over server-originated frames —
    notifications AND agent-initiated requests both arrive here. The caller
    distinguishes by checking for the `id` and `method` keys.

The client allocates client-side request ids autonomously starting at 1.
The bridge (hermes-bridge) is responsible for translating those into
session-unique ids when it forwards to hermes-acp; we don't see that.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class ACPError(Exception):
    """Raised when the agent returns a JSON-RPC error response to a request."""

    def __init__(self, error: dict[str, Any]) -> None:
        self.code: int = error.get("code", 0)
        self.message: str = error.get("message", "")
        self.data: Any = error.get("data")
        super().__init__(f"ACP error {self.code}: {self.message}")


class ACPClient:
    def __init__(self, url: str) -> None:
        self._url = url
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()

    async def connect(self) -> None:
        if self._ws is not None:
            raise RuntimeError("ACPClient already connected")
        logger.info("connecting to %s", self._url)
        self._ws = await websockets.connect(self._url)
        self._reader_task = asyncio.create_task(self._reader(), name="acp-reader")

    async def close(self) -> None:
        self._closed.set()
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    @property
    def closed(self) -> bool:
        return self._closed.is_set() or (
            self._reader_task is not None and self._reader_task.done()
        )

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._ws is None:
            raise RuntimeError("ACPClient not connected")
        req_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        envelope: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            envelope["params"] = params
        await self._ws.send(json.dumps(envelope))

        try:
            response = await future
        finally:
            self._pending.pop(req_id, None)

        if "error" in response:
            raise ACPError(response["error"])
        return response.get("result")

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._ws is None:
            raise RuntimeError("ACPClient not connected")
        envelope: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            envelope["params"] = params
        await self._ws.send(json.dumps(envelope))

    async def respond(self, request_id: Any, result: Any) -> None:
        """Respond to an agent-initiated request that we received via incoming()."""
        if self._ws is None:
            raise RuntimeError("ACPClient not connected")
        envelope = {"jsonrpc": "2.0", "id": request_id, "result": result}
        await self._ws.send(json.dumps(envelope))

    async def respond_error(self, request_id: Any, code: int, message: str) -> None:
        if self._ws is None:
            raise RuntimeError("ACPClient not connected")
        envelope = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
        await self._ws.send(json.dumps(envelope))

    async def incoming(self) -> AsyncIterator[dict[str, Any]]:
        """Yield server-originated frames forever. Stops when the connection closes."""
        while not self.closed:
            try:
                msg = await asyncio.wait_for(self._incoming.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            yield msg

    async def _reader(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("non-json frame: %r", raw[:120])
                    continue
                if not isinstance(msg, dict):
                    continue

                # Response to one of our outgoing requests: id present, no method.
                if "id" in msg and "method" not in msg:
                    fut = self._pending.get(msg["id"])
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                    continue

                # Notification or agent-initiated request — pass to consumer.
                await self._incoming.put(msg)
        except (asyncio.CancelledError, ConnectionClosed):
            pass
        except Exception:
            logger.exception("reader task crashed")
        finally:
            self._closed.set()
            # Fail any in-flight requests so callers don't hang.
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(ConnectionError("ACP connection closed"))
            self._pending.clear()
