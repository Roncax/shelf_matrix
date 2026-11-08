import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from shelf_matrix.client.catalog import load_catalog
from shelf_matrix.protocol import PREVIEW_BYTES, encode, encode_preview
from shelf_matrix.server.app import create_app

PROGRAMS = Path(__file__).resolve().parents[2] / "programs"
TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def http(tmp_path):
    app = create_app(data_dir=tmp_path, token=TOKEN, start_weather=False)
    with TestClient(app) as c:
        yield c


def hello(ws):
    catalog = [m.to_dict() for m in load_catalog(PROGRAMS).values()]
    ws.send_text(encode("hello", client_version="t", catalog=catalog,
                        state={"active_program": "demo-life", "params": {}, "globals": {}}))


def recv(ws):
    return json.loads(ws.receive_text())


def recv_kind(ws, kind):
    for _ in range(20):
        m = recv(ws)
        if m["kind"] == kind:
            return m
    raise AssertionError(f"no {kind} message")


def test_healthz_and_index(http):
    assert http.get("/healthz").json() == {"ok": True}
    assert "Shelf Matrix" in http.get("/").text


def test_missing_token_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SHELF_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        create_app(data_dir=tmp_path)


def test_client_ws_rejects_bad_token(http):
    with http.websocket_connect("/ws/client", headers={"Authorization": "Bearer nope"}) as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4401


def test_client_ws_rejects_missing_token(http):
    with http.websocket_connect("/ws/client") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4401


def test_ui_receives_state_on_connect(http):
    with http.websocket_connect("/ws/ui") as ui:
        m = recv(ui)
    assert m["kind"] == "state"
    assert m["client"]["online"] is False


def test_ui_select_program_reaches_client(http):
    with http.websocket_connect("/ws/client", headers=AUTH) as pi:
        hello(pi)
        with http.websocket_connect("/ws/ui") as ui:
            recv_kind(ui, "state")
            ui.send_text(encode("select_program", program="demo-life"))
            m = recv_kind(pi, "set_program")
    assert m["program"] == "demo-life"


def test_ui_bad_message_gets_error(http):
    with http.websocket_connect("/ws/ui") as ui:
        recv(ui)
        ui.send_text("not json")
        assert recv(ui)["kind"] == "error"


def test_changes_while_offline_are_synced_on_reconnect(http):
    with http.websocket_connect("/ws/client", headers=AUTH) as pi:
        hello(pi)
        recv_kind(pi, "sync")
    with http.websocket_connect("/ws/ui") as ui:
        recv(ui)
        ui.send_text(encode("update_globals", globals={"brightness": 15}))
        state = recv_kind(ui, "state")
        assert state["client"]["online"] is False
        assert state["globals"]["brightness"] == 15
    with http.websocket_connect("/ws/client", headers=AUTH) as pi:
        hello(pi)
        assert recv_kind(pi, "sync")["globals"]["brightness"] == 15


def test_preview_relayed_to_ui(http):
    with http.websocket_connect("/ws/ui") as ui:
        recv(ui)
        with http.websocket_connect("/ws/client", headers=AUTH) as pi:
            frame = encode_preview(bytes(PREVIEW_BYTES))
            pi.send_bytes(b"junk")
            pi.send_text("junk")
            pi.send_bytes(frame)
            for _ in range(10):
                m = ui.receive()
                if m.get("bytes"):
                    break
            assert m["bytes"] == frame


def test_ui_binary_frame_is_not_fatal(http):
    with http.websocket_connect("/ws/ui") as ui:
        recv(ui)
        ui.send_bytes(b"\x00junk")
        assert recv(ui)["kind"] == "error"
        ui.send_text(encode("update_globals", globals={"brightness": 20}))
        assert recv_kind(ui, "state")["globals"]["brightness"] == 20


def test_newer_client_replaces_older(http):
    with http.websocket_connect("/ws/ui") as ui:
        recv(ui)
        with http.websocket_connect("/ws/client", headers=AUTH) as old:
            hello(old)
            recv_kind(old, "sync")
            with http.websocket_connect("/ws/client", headers=AUTH) as new:
                hello(new)
                recv_kind(new, "sync")
                with pytest.raises(WebSocketDisconnect) as e:
                    for _ in range(20):
                        old.receive_text()
                assert e.value.code == 4000
                # a late frame from the replaced Pi must not reach the hub
                old.send_text(encode("status", marker="stale"))
                new.send_text(encode("status", marker="fresh"))
                for _ in range(20):
                    st = recv(ui)
                    if st["kind"] == "state" and st["client"]["status"]:
                        break
                assert st["client"]["online"] is True
                assert st["client"]["status"]["marker"] == "fresh"
