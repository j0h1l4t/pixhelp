"""WebSocket server for the Flutter overlay (specs.md §3.4): loopback, token
auth, one client (latest wins — a stale toast is worse than no toast)."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time

import websockets
from websockets.exceptions import ConnectionClosed

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SEND_TIMEOUT_S = 2.0


class IpcServer:
    def __init__(self, host: str, port: int, token: str):
        self.host = host
        self.port = port
        self.token = token
        self._client: websockets.ServerConnection | None = None
        self._server = None
        self.dropped = 0
        self.sent = 0

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handler, self.host, self.port, ping_interval=20.0
        )
        log.info("IPC listening on ws://%s:%d", self.host, self.port)

    async def _handler(self, ws) -> None:
        try:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
        except (TimeoutError, json.JSONDecodeError, ConnectionClosed):
            await ws.close(code=1008, reason="expected hello")
            return
        if (
            hello.get("type") != "hello"
            or hello.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION
            or not isinstance(hello.get("token"), str)
            or not hmac.compare_digest(hello["token"], self.token)
        ):
            await ws.close(code=1008, reason="auth failed")
            log.info("IPC rejected client (bad hello/token)")
            return
        if self._client is not None:
            await self._client.close(code=1000, reason="superseded")
        self._client = ws
        log.info("IPC client connected")
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "pong":
                    continue
        except ConnectionClosed:
            pass
        finally:
            if self._client is ws:
                self._client = None
            log.info("IPC client disconnected")

    async def send_toast(
        self,
        rule_id: str,
        title: str,
        body: str,
        anchor_rect: dict | None,
        severity: str,
        ttl_s: float,
    ) -> bool:
        ws = self._client
        if ws is None:
            return False
        msg = {
            "schema_version": SCHEMA_VERSION,
            "type": "toast",
            "id": rule_id,
            "title": title,
            "body": body,
            "anchor_rect": anchor_rect,
            "severity": severity,
            "ttl_s": ttl_s,
            "ts": time.time(),
        }
        try:
            await asyncio.wait_for(ws.send(json.dumps(msg)), timeout=SEND_TIMEOUT_S)
        except (TimeoutError, ConnectionClosed):
            self.dropped += 1  # latest-wins: never queue behind a stalled client
            return False
        self.sent += 1
        return True

    async def clear(self) -> None:
        ws = self._client
        if ws is None:
            return
        try:
            await asyncio.wait_for(
                ws.send(
                    json.dumps(
                        {"schema_version": SCHEMA_VERSION, "type": "clear"}
                    )
                ),
                timeout=SEND_TIMEOUT_S,
            )
        except (TimeoutError, ConnectionClosed):
            self.dropped += 1

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.close(code=1001, reason="server shutdown")
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
