"""FastAPI app: static UI, health check and the two WebSocket endpoints."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from shelf_matrix.protocol import ProtocolError, decode, encode
from shelf_matrix.server.hub import Hub
from shelf_matrix.server.store import Store
from shelf_matrix.server.weather import WeatherService

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
CLOSE_UNAUTHORIZED = 4401
CLOSE_REPLACED = 4000


class _WsPeer:
    """Adapts a FastAPI WebSocket to the hub's Peer protocol."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws

    async def send_text(self, text: str) -> None:
        await self.ws.send_text(text)

    async def send_bytes(self, data: bytes) -> None:
        await self.ws.send_bytes(data)


def _authorized(ws: WebSocket, token: str) -> bool:
    header = ws.headers.get("authorization", "")
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return secrets.compare_digest(supplied.encode(), token.encode())


def create_app(data_dir: Path | None = None, token: str | None = None,
               start_weather: bool = True) -> FastAPI:
    token = token if token is not None else os.environ.get("SHELF_TOKEN")
    if not token:
        raise RuntimeError("SHELF_TOKEN is not set")
    data_dir = Path(data_dir if data_dir is not None else os.environ.get("SHELF_DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    hub = Hub(Store(data_dir / "store.json"))
    current_client: list[_WsPeer] = []  # at most one: the live Pi connection

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not start_weather:
            yield
            return
        async with httpx.AsyncClient() as http:
            weather = WeatherService(hub, http)
            task = asyncio.create_task(weather.run())
            try:
                yield
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                await weather.close()

    app = FastAPI(lifespan=lifespan)
    app.state.hub = hub

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.websocket("/ws/client")
    async def ws_client(ws: WebSocket) -> None:
        await ws.accept()
        if not _authorized(ws, token):
            await ws.close(code=CLOSE_UNAUTHORIZED)
            return
        peer = _WsPeer(ws)
        old = current_client[0] if current_client else None
        current_client[:] = [peer]
        await hub.client_connected(peer)
        if old is not None:
            with contextlib.suppress(Exception):
                await old.ws.close(code=CLOSE_REPLACED)
        try:
            while True:
                frame = await ws.receive()
                if frame["type"] == "websocket.disconnect":
                    break
                if frame.get("bytes") is not None:
                    await hub.on_client_preview(frame["bytes"])
                elif frame.get("text") is not None:
                    try:
                        msg = decode(frame["text"])
                    except ProtocolError as e:
                        logger.warning("dropping invalid client message: %s", e)
                        continue
                    await hub.on_client_message(msg)
        except WebSocketDisconnect:
            pass
        finally:
            if current_client and current_client[0] is peer:
                current_client.clear()
            await hub.client_disconnected(peer)

    @app.websocket("/ws/ui")
    async def ws_ui(ws: WebSocket) -> None:
        await ws.accept()
        peer = _WsPeer(ws)
        await hub.browser_connected(peer)
        try:
            while True:
                text = await ws.receive_text()
                try:
                    msg = decode(text)
                except ProtocolError as e:
                    await peer.send_text(encode("error", message=str(e)))
                    continue
                await hub.on_browser_message(peer, msg)
        except (WebSocketDisconnect, RuntimeError):  # peer gone, incl. failed send
            pass
        finally:
            await hub.browser_disconnected(peer)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
