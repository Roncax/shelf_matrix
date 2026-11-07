import asyncio
import logging

import pytest

from shelf_matrix.protocol import PREVIEW_BYTES, Message, decode, encode_preview
from shelf_matrix.server.hub import Hub
from shelf_matrix.server.store import Store

CLOCK = {
    "id": "clock", "name": "Clock", "kind": "python", "entry": "program:Clock",
    "params": [
        {"name": "color", "label": "Color", "type": "color", "default": "#ff9a3c"},
        {"name": "style", "label": "Style", "type": "choice", "default": "digital",
         "options": ["digital", "analog"]},
        {"name": "location", "label": "Location", "type": "text", "default": "", "max_len": 80},
    ],
}
SOLID = {
    "id": "solid", "name": "Solid", "kind": "python", "entry": "program:Solid",
    "params": [{"name": "color", "label": "Color", "type": "color", "default": "#ffffff"}],
}


class FakePeer:
    def __init__(self, fail=False):
        self.msgs = []
        self.frames = []
        self.fail = fail

    async def send_text(self, text):
        if self.fail:
            raise ConnectionError("closed")
        self.msgs.append(decode(text))

    async def send_bytes(self, data):
        if self.fail:
            raise ConnectionError("closed")
        self.frames.append(data)

    def kinds(self):
        return [m.kind for m in self.msgs]

    def last(self, kind):
        return [m for m in self.msgs if m.kind == kind][-1]


def hello(active="solid", params=None, globals_=None, catalog=(CLOCK, SOLID)):
    return Message("hello", {
        "client_version": "0.1.0",
        "catalog": list(catalog),
        "state": {"active_program": active, "params": params or {}, "globals": globals_ or {}},
    })


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "store.json")


async def connected_hub(store, **hello_kw):
    hub = Hub(store)
    pi = FakePeer()
    await hub.client_connected(pi)
    await hub.on_client_message(hello(**hello_kw))
    pi.msgs.clear()
    return hub, pi


async def test_first_hello_adopts_client_state(store, tmp_path):
    hub = Hub(store)
    pi = FakePeer()
    await hub.client_connected(pi)
    await hub.on_client_message(hello(active="solid", params={"solid": {"color": "#00ff00"}},
                                      globals_={"brightness": 30}))
    assert store.active_program == "solid"
    sync = pi.last("sync").payload
    assert sync["active_program"] == "solid"
    assert sync["params"]["solid"] == {"color": "#00ff00"}
    assert sync["params"]["clock"] == {"color": "#ff9a3c", "style": "digital", "location": ""}
    assert sync["globals"] == {"brightness": 30}
    # persisted
    again = Store(tmp_path / "store.json")
    assert again.active_program == "solid"
    assert [m["id"] for m in again.catalog] == ["clock", "solid"]


async def test_later_hello_gets_server_state_via_sync(store):
    store.active_program = "clock"
    store.params = {"clock": {"style": "analog"}}
    hub = Hub(store)
    pi = FakePeer()
    await hub.client_connected(pi)
    await hub.on_client_message(hello(active="solid", params={"clock": {"style": "digital"}}))
    sync = pi.last("sync").payload
    assert sync["active_program"] == "clock"
    assert sync["params"]["clock"]["style"] == "analog"
    assert store.active_program == "clock"


async def test_hello_broadcasts_state_and_preview_on_after_sync(store):
    hub = Hub(store)
    browser = FakePeer()
    await hub.browser_connected(browser)
    pi = FakePeer()
    await hub.client_connected(pi)
    await hub.on_client_message(hello())
    assert pi.kinds() == ["sync", "preview_on"]
    state = browser.last("state").payload
    assert state["active_program"] == "solid"
    assert state["client"]["online"] is True
    assert state["client"]["last_seen"] is not None


async def test_update_params_validates(store):
    hub, pi = await connected_hub(store)
    browser = FakePeer()
    await hub.browser_connected(browser)
    pi.msgs.clear()
    browser.msgs.clear()
    await hub.on_browser_message(browser, Message("update_params", {
        "program": "clock", "params": {"color": "red"}}))
    assert browser.kinds() == ["error"]
    assert "color" in browser.last("error").payload["message"]
    assert pi.msgs == []
    assert store.params.get("clock", {}).get("color") is None


@pytest.mark.parametrize("payload", [
    {"program": "nope", "params": {"color": "#000000"}},
    {"program": "clock", "params": {"bogus": 1}},
    {"program": "clock", "params": ["color"]},
    {"program": "clock", "params": {"color": "#000000", "style": "fancy"}},
    {"params": {"color": "#000000"}},
])
async def test_update_params_rejects_bad_input(store, payload):
    hub, pi = await connected_hub(store)
    browser = FakePeer()
    await hub.browser_connected(browser)
    pi.msgs.clear()
    browser.msgs.clear()
    await hub.on_browser_message(browser, Message("update_params", payload))
    assert browser.kinds() == ["error"]
    assert pi.msgs == []
    assert store.params.get("clock", {}) == {}


async def test_update_params_pushes_to_client_and_broadcasts(store):
    hub, pi = await connected_hub(store)
    b1, b2 = FakePeer(), FakePeer()
    await hub.browser_connected(b1)
    await hub.browser_connected(b2)
    calls = []

    async def cb():
        calls.append(1)

    hub.on_params_changed.append(cb)
    await hub.on_browser_message(b1, Message("update_params", {
        "program": "clock", "params": {"color": "#00FF00"}}))
    await hub.on_browser_message(b1, Message("update_params", {
        "program": "clock", "params": {"style": "analog"}}))
    sp = pi.last("set_params").payload
    # full resolved dict, merged with the earlier partial update
    assert sp == {"program": "clock",
                  "params": {"color": "#00ff00", "style": "analog", "location": ""}}
    assert store.params["clock"] == {"color": "#00ff00", "style": "analog"}
    for b in (b1, b2):
        assert b.last("state").payload["params"]["clock"]["style"] == "analog"
    assert calls == [1, 1]


async def test_update_globals(store):
    hub, pi = await connected_hub(store)
    browser = FakePeer()
    await hub.browser_connected(browser)
    await hub.on_browser_message(browser, Message("update_globals", {"globals": {"brightness": 0}}))
    assert browser.last("error")
    assert "set_globals" not in pi.kinds()
    await hub.on_browser_message(browser, Message("update_globals", {"globals": {"brightness": 25}}))
    assert pi.last("set_globals").payload == {"globals": {"brightness": 25}}
    assert store.globals == {"brightness": 25}
    assert browser.last("state").payload["globals"] == {"brightness": 25}


async def test_select_program(store):
    hub, pi = await connected_hub(store)
    browser = FakePeer()
    await hub.browser_connected(browser)
    await hub.on_browser_message(browser, Message("select_program", {"program": "nope"}))
    assert browser.kinds()[-1] == "error"
    assert "set_program" not in pi.kinds()
    await hub.on_browser_message(browser, Message("select_program", {"program": "clock"}))
    assert pi.last("set_program").payload == {
        "program": "clock",
        "params": {"color": "#ff9a3c", "style": "digital", "location": ""}}
    assert store.active_program == "clock"
    assert browser.last("state").payload["active_program"] == "clock"


async def test_unknown_browser_message_gets_error(store):
    hub, pi = await connected_hub(store)
    browser = FakePeer()
    await hub.browser_connected(browser)
    pi.msgs.clear()
    await hub.on_browser_message(browser, Message("reboot", {}))
    assert browser.kinds()[-1] == "error"
    assert pi.msgs == []


async def test_preview_on_off_follows_browsers(store):
    hub, pi = await connected_hub(store)
    b1, b2 = FakePeer(), FakePeer()
    await hub.browser_connected(b1)
    assert b1.kinds() == ["state"]
    await hub.browser_connected(b2)
    assert pi.kinds() == ["preview_on"]
    await hub.browser_disconnected(b1)
    assert pi.kinds() == ["preview_on"]
    await hub.browser_disconnected(b2)
    assert pi.kinds() == ["preview_on", "preview_off"]


async def test_preview_frames_forwarded_to_browsers(store):
    hub, pi = await connected_hub(store)
    b1, b2 = FakePeer(), FakePeer()
    await hub.browser_connected(b1)
    await hub.browser_connected(b2)
    frame = encode_preview(bytes(PREVIEW_BYTES))
    await hub.on_client_preview(frame)
    await hub.on_client_preview(b"garbage")
    assert b1.frames == [frame] and b2.frames == [frame]


async def test_dead_browser_dropped_from_broadcast(store, caplog):
    hub, pi = await connected_hub(store)
    good, dead = FakePeer(), FakePeer()
    await hub.browser_connected(good)
    await hub.browser_connected(dead)
    dead.fail = True
    with caplog.at_level(logging.WARNING):
        await hub.on_client_message(Message("status", {"program": "solid", "state": "running"}))
    assert good.last("state").payload["client"]["status"] == {"program": "solid", "state": "running"}
    pi.msgs.clear()
    await hub.browser_disconnected(good)
    # the dead one was already dropped, so the last real browser leaving turns preview off
    assert pi.kinds() == ["preview_off"]


async def test_cached_data_resent_after_sync(store):
    hub, pi = await connected_hub(store)
    await hub.publish_data("weather", "current", {"temp": 12})
    await hub.publish_data("weather", "current", {"temp": 13})
    assert pi.last("data").payload == {"topic": "weather", "key": "current", "value": {"temp": 13}}
    await hub.client_disconnected(pi)
    await hub.publish_data("weather", "today", {"max": 15})  # offline: cached only
    pi2 = FakePeer()
    await hub.client_connected(pi2)
    await hub.on_client_message(hello())
    assert pi2.kinds() == ["sync", "data", "data"]
    data = {(m.payload["key"]): m.payload["value"] for m in pi2.msgs if m.kind == "data"}
    assert data == {"current": {"temp": 13}, "today": {"max": 15}}


async def test_client_replacement_ignores_stale_disconnect(store):
    hub, old = await connected_hub(store)
    browser = FakePeer()
    await hub.browser_connected(browser)
    new = FakePeer()
    await hub.client_connected(new)
    await hub.client_disconnected(old)
    assert hub.state_payload()["client"]["online"] is True
    await hub.publish_data("t", "k", 1)
    assert new.kinds() == ["data"] and "data" not in old.kinds()
    await hub.client_disconnected(new)
    assert hub.state_payload()["client"]["online"] is False
    assert browser.last("state").payload["client"]["online"] is False


async def test_callback_exception_does_not_break_hub(store, caplog):
    hub = Hub(store)
    calls = []

    async def bad():
        raise RuntimeError("boom")

    async def good():
        calls.append(1)

    hub.on_params_changed.extend([bad, good])
    pi = FakePeer()
    await hub.client_connected(pi)
    with caplog.at_level(logging.ERROR):
        await hub.on_client_message(hello())
    assert calls == [1]
    assert "boom" in caplog.text
    assert pi.kinds() == ["sync"]


@pytest.mark.parametrize("msg", [
    Message("hello", {"catalog": "x", "state": None}),
    Message("hello", {"catalog": [{"id": 1}, "junk", {"name": "x"}], "state": "x"}),
    Message("status", {}),
    Message("whatever", {"a": 1}),
])
async def test_bad_client_input_never_raises(store, msg):
    hub = Hub(store)
    pi = FakePeer()
    await hub.client_connected(pi)
    await hub.on_client_message(msg)
    hub.state_payload()


async def test_hello_skips_invalid_manifests(store):
    bad = {"id": "bad", "name": "Bad", "kind": "python",
           "params": [{"name": "n", "label": "N", "type": "int", "default": 5, "min": "x"}]}
    hub, pi = await connected_hub(store, catalog=(CLOCK, bad, "junk"))
    assert [m["id"] for m in store.catalog] == ["clock"]
    assert hub.state_payload()["catalog"][0]["id"] == "clock"


async def test_state_payload_shape(store):
    hub = Hub(store)
    p = hub.state_payload()
    assert p == {"catalog": [], "active_program": None, "params": {},
                 "globals": {"brightness": 70},
                 "client": {"online": False, "last_seen": None, "status": None}}


class YieldingPeer(FakePeer):
    async def send_text(self, text):
        await asyncio.sleep(0)
        await super().send_text(text)


async def test_concurrent_browser_connects_send_one_preview_on(store):
    hub, pi = await connected_hub(store)
    b1, b2 = YieldingPeer(), YieldingPeer()
    await asyncio.gather(hub.browser_connected(b1), hub.browser_connected(b2))
    assert pi.kinds() == ["preview_on"]
    assert b1.kinds() == ["state"] and b2.kinds() == ["state"]
    await asyncio.gather(hub.browser_disconnected(b1), hub.browser_disconnected(b2))
    assert pi.kinds() == ["preview_on", "preview_off"]


async def test_persist_failure_does_not_break_hub(store, monkeypatch, caplog):
    hub, pi = await connected_hub(store)

    def boom():
        raise RuntimeError("disk gone")

    monkeypatch.setattr(store, "save", boom)
    browser = FakePeer()
    await hub.browser_connected(browser)
    with caplog.at_level(logging.WARNING):
        await hub.on_browser_message(browser, Message("select_program", {"program": "clock"}))
    assert pi.last("set_program").payload["program"] == "clock"
    assert "error" not in browser.kinds()
    assert "disk gone" in caplog.text


async def test_active_program_missing_from_catalog_is_kept(store):
    store.active_program = "gone"
    hub, pi = await connected_hub(store)
    hub2_pi = FakePeer()
    await hub.client_connected(hub2_pi)
    await hub.on_client_message(hello(catalog=(CLOCK, SOLID)))
    assert hub2_pi.last("sync").payload["active_program"] == "gone"
    assert hub.state_payload()["active_program"] == "gone"
    assert store.active_program == "gone"
    gone = dict(SOLID, id="gone", name="Gone")
    await hub.on_client_message(hello(catalog=(CLOCK, SOLID, gone)))
    sync = hub2_pi.last("sync").payload
    assert sync["active_program"] == "gone" and "gone" in sync["params"]
