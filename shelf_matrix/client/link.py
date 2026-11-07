"""Outbound WebSocket link from the Pi to the server, with reconnect + backoff."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from websockets.asyncio.client import ClientConnection, connect

from shelf_matrix.protocol import Message, ProtocolError, decode, encode

logger = logging.getLogger(__name__)

# A send that cannot complete in this time is dropped: callers (the supervisor's
# status/preview path) must never be stalled by a slow or dead socket.
SEND_TIMEOUT_S = 1.0
OPEN_TIMEOUT_S = 10.0


class ServerLink:
    def __init__(
        self,
        url: str,
        token: str,
        on_connect: Callable[[], Awaitable[None]],
        on_message: Callable[[Message], Awaitable[None]],
        *,
        backoff: tuple[float, ...] = (1, 2, 5, 10, 30, 60),
    ) -> None:
        self._url = url
        self._token = token
        self._on_connect = on_connect
        self._on_message = on_message
        self._backoff = backoff
        self._ws: ClientConnection | None = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def run(self) -> None:
        failures = 0
        while True:
            try:
                async with connect(
                    self._url,
                    additional_headers={"Authorization": f"Bearer {self._token}"},
                    open_timeout=OPEN_TIMEOUT_S,
                ) as ws:
                    self._ws = ws
                    failures = 0
                    logger.info("connected to %s", self._url)
                    await self._on_connect()
                    async for raw in ws:
                        await self._dispatch(raw)
                logger.info("server closed the connection")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("server link error: %s: %s", type(e).__name__, e)
            finally:
                self._ws = None
            delay = self._backoff[min(failures, len(self._backoff) - 1)]
            failures += 1
            await asyncio.sleep(delay)

    async def _dispatch(self, raw: str | bytes) -> None:
        try:
            msg = decode(raw)
        except ProtocolError as e:
            logger.warning("ignoring bad message from server: %s", e)
            return
        try:
            await self._on_message(msg)
        except Exception:
            logger.exception("handling %r message failed", msg.kind)

    async def send_json(self, kind: str, **payload) -> bool:
        try:
            text = encode(kind, **payload)
        except (TypeError, ValueError) as e:
            logger.warning("cannot encode %r message: %s", kind, e)
            return False
        return await self._send(text)

    async def send_bytes(self, data: bytes) -> bool:
        return await self._send(bytes(data))

    async def _send(self, frame: str | bytes) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            await asyncio.wait_for(ws.send(frame), SEND_TIMEOUT_S)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:  # closed, timed out, ...: the run loop reconnects
            logger.debug("send failed: %s: %s", type(e).__name__, e)
            return False
