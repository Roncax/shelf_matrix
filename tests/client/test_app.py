import asyncio
import contextlib
import socket
import time
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from shelf_matrix.client.app import ClientApp
from shelf_matrix.client.state import LocalState, load_state, save_state
from shelf_matrix.config import load_client_config
from shelf_matrix.protocol import decode, encode

REPO = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_config(tmp_path, port):
    programs = tmp_path / "programs"
    programs.mkdir()
    for root in (REPO / "programs", REPO / "tests" / "fixtures" / "programs"):
        for d in root.iterdir():
            if (d / "manifest.toml").is_file():
                (programs / d.name).symlink_to(d)
    p = tmp_path / "client.toml"
    p.write_text(
        f'server_url = "ws://127.0.0.1:{port}/ws/client"\n'
        'token = "t0ken"\n'
        f'library_dir = "{tmp_path}"\n'
        f'programs_dir = "{programs}"\n'
        f'fonts_dir = "{REPO / "fonts"}"\n'
        f'state_path = "{tmp_path / "state" / "state.json"}"\n'
    )
    return load_client_config(p)


async def wait_for(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


class FakeServer:
    """Records each connection's Authorization header and incoming messages."""

    def __init__(self, port):
        self.port = port
        self.headers: list[str | None] = []
        self.received: list = []
        self.conns: list = []
        self._server = None

    async def _handler(self, conn):
        self.headers.append(conn.request.headers.get("Authorization"))
        self.conns.append(conn)
        async for raw in conn:
            if isinstance(raw, str):
                self.received.append(decode(raw))

    async def start(self):
        self._server = await serve(self._handler, "127.0.0.1", self.port)

    async def stop(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def hellos(self):
        return [m for m in self.received if m.kind == "hello"]

    async def send(self, kind, **payload):
        await self.conns[-1].send(encode(kind, **payload))


@pytest.fixture(autouse=True)
def fake_matrix(monkeypatch):
    monkeypatch.setenv("SHELF_MATRIX_FAKE", "1")


@contextlib.asynccontextmanager
async def running(app):
    task = asyncio.create_task(app.run())
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert app.supervisor is None or app.supervisor.current is None


@pytest.fixture
def config_unreachable(tmp_path):
    return make_config(tmp_path, free_port())


@pytest.fixture
async def fake_server(tmp_path):
    port = free_port()
    server = FakeServer(port)
    await server.start()
    server.config = make_config(tmp_path, port)
    yield server
    await server.stop()


async def test_starts_cached_program_without_server(config_unreachable):
    config = config_unreachable
    save_state(config.state_path, LocalState(active_program="solid"))
    app = ClientApp(config, link_backoff=(0.1,))
    async with running(app) as task:
        await wait_for(lambda: app.supervisor is not None and app.supervisor.current == "solid")
        await wait_for(lambda: app.supervisor._proc is not None)
        await asyncio.sleep(0.5)  # several failed connects later, still running
        assert not task.done()
        assert app.supervisor.current == "solid"
        assert not app.link.connected


async def test_sends_hello_with_catalog_and_token(fake_server):
    app = ClientApp(fake_server.config, link_backoff=(0.1,))
    async with running(app):
        await wait_for(lambda: fake_server.hellos())
        assert app.link.connected
    assert fake_server.headers[0] == "Bearer t0ken"
    first = fake_server.received[0]
    assert first.kind == "hello"
    ids = [m["id"] for m in first.payload["catalog"]]
    assert "clock" in ids and "solid" in ids
    assert first.payload["state"]["active_program"] == "clock"
    assert set(first.payload["state"]) == {"active_program", "params", "globals"}


async def test_sync_switches_program_and_persists(fake_server):
    config = fake_server.config
    app = ClientApp(config, link_backoff=(0.1,))
    async with running(app):
        await wait_for(lambda: fake_server.hellos())
        await fake_server.send(
            "sync", active_program="solid",
            params={"solid": {"color": "#00ff00"}}, globals={"brightness": 20})
        await wait_for(lambda: app.supervisor.current == "solid")
        await wait_for(lambda: load_state(config.state_path).active_program == "solid")
        state = load_state(config.state_path)
        assert state.params["solid"]["color"] == "#00ff00"
        assert state.globals == {"brightness": 20}
        # supervisor status is forwarded to the server
        await wait_for(lambda: any(
            m.kind == "status" and m.payload["active_program"] == "solid"
            and m.payload["state"] == "running" for m in fake_server.received))


async def test_set_params_for_other_program_only_stores(fake_server):
    config = fake_server.config
    save_state(config.state_path, LocalState(active_program="solid"))
    app = ClientApp(config, link_backoff=(0.1,))
    async with running(app):
        await wait_for(lambda: fake_server.hellos())
        await fake_server.send("set_params", program="clock", params={"style": "analog"})
        await wait_for(lambda: "clock" in load_state(config.state_path).params)
        assert app.supervisor.current == "solid"
        assert load_state(config.state_path).params["clock"] == {"style": "analog"}


async def test_set_program_and_data_persist(fake_server):
    config = fake_server.config
    app = ClientApp(config, link_backoff=(0.1,))
    async with running(app):
        await wait_for(lambda: fake_server.hellos())
        await fake_server.send("set_program", program="solid", params={"color": "#0000ff"})
        await fake_server.send("data", topic="weather", key="home", value={"temp": 3})
        await wait_for(lambda: load_state(config.state_path).data.get("weather"))
        state = load_state(config.state_path)
        assert state.active_program == "solid"
        assert state.params["solid"] == {"color": "#0000ff"}
        assert state.data == {"weather": {"home": {"temp": 3}}}
        assert app.supervisor.current == "solid"


async def test_bad_messages_are_skipped(fake_server):
    app = ClientApp(fake_server.config, link_backoff=(0.1,))
    async with running(app):
        await wait_for(lambda: fake_server.hellos())
        await fake_server.conns[-1].send("not json")
        await fake_server.send("set_params", program=5, params="x")
        await fake_server.send("set_program", program="solid", params={})
        await wait_for(lambda: app.supervisor.current == "solid")
        assert len(fake_server.hellos()) == 1  # same connection survived


async def test_reconnects_after_server_restart(fake_server):
    app = ClientApp(fake_server.config, link_backoff=(0.1,))
    async with running(app):
        await wait_for(lambda: len(fake_server.hellos()) == 1)
        await fake_server.stop()
        await wait_for(lambda: not app.link.connected)
        assert await app.link.send_json("status", state="x") is False
        await fake_server.start()
        await wait_for(lambda: len(fake_server.hellos()) == 2, timeout=10)
        assert fake_server.headers == ["Bearer t0ken", "Bearer t0ken"]
