"""Outbound WebSocket link from the Pi to the server, with reconnect + backoff."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from websockets.asyncio.client import ClientConnection, connect

from shelf_matrix.protocol import Message, ProtocolError, decode, encode

logger = logging.getLogger(__name__)

# A send that cannot complete in this time is dropped: callers (the supervisor's
# status/preview path) must never be stalled by a slow or dead socket.
SEND_TIMEOUT_S = 1.0
OPEN_TIMEOUT_S = 10.0
# A session counts as healthy (resets the backoff) once the server has sent a
# message or the connection has stayed up this long.
STABLE_AFTER_S = 10.0
AUTH_REJECTED = (4401, 4403)


class ServerLink:
    def __init__(
        self,
        url: str,
        token: str,
        on_connect: Callable[[], Awaitable[None]],
        on_message: Callable[[Message], Awaitable[None]],
        *,
        backoff: tuple[float, ...] = (1, 2, 5, 10, 30, 60),
        on_disconnect: Callable[[], Awaitable[None]] | None = None,
        stable_after_s: float = STABLE_AFTER_S,
    ) -> None:
        self._url = url
        self._token = token
        self._on_connect = on_connect
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._backoff = backoff
        self._stable_after_s = stable_after_s
        self._ws: ClientConnection | None = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def run(self) -> None:
        failures = 0
        token_warned = False
        while True:
            opened_at: float | None = None
            close_code: int | None = None
            got_message = False
            try:
                async with connect(
                    self._url,
                    additional_headers={"Authorization": f"Bearer {self._token}"},
                    open_timeout=OPEN_TIMEOUT_S,
                ) as ws:
                    self._ws = ws
                    opened_at = time.monotonic()
                    logger.info("connected to %s", self._url)
                    try:
                        await self._on_connect()
                        async for raw in ws:
                            got_message = True
                            await self._dispatch(raw)
                    finally:
                        close_code = ws.close_code
                error = None
            except asyncio.CancelledError:
                raise
            except Exception as e:
                error = e
            finally:
                self._ws = None
            if close_code in AUTH_REJECTED:
                if not token_warned:
                    logger.warning("server rejected the token (close code %s)", close_code)
                    token_warned = True
            elif error is not None:
                logger.warning("server link error: %s: %s", type(error).__name__, error)
            else:
                logger.info("server closed the connection (code %s)", close_code)
            if opened_at is not None:
                await self._notify_disconnect()
                # Only a session that proved healthy resets the backoff, so a
                # server that accepts and then closes at once (e.g. bad token)
                # doesn't get hammered at the fastest rate forever.
                healthy = close_code not in AUTH_REJECTED and (
                    got_message or time.monotonic() - opened_at >= self._stable_after_s)
                if healthy:
                    failures = 0
                    token_warned = False
            delay = self._backoff[min(failures, len(self._backoff) - 1)]
            failures += 1
            await asyncio.sleep(delay)

    async def _notify_disconnect(self) -> None:
        if self._on_disconnect is None:
            return
        try:
            await self._on_disconnect()
        except Exception:
            logger.exception("on_disconnect callback failed")

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
