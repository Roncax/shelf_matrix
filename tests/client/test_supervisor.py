import asyncio
import os
import time
from pathlib import Path

import pytest

from shelf_matrix.client.catalog import load_catalog
from shelf_matrix.client.supervisor import Supervisor, build_command
from shelf_matrix.config import load_client_config
from shelf_matrix.schema import load_manifest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def config(tmp_path):
    p = tmp_path / "client.toml"
    p.write_text(
        'server_url = "ws://localhost:1"\n'
        'token = "t"\n'
        f'library_dir = "{tmp_path}"\n'
        f'programs_dir = "{REPO / "programs"}"\n'
        f'fonts_dir = "{REPO / "fonts"}"\n'
        f'state_path = "{tmp_path / "state" / "state.json"}"\n'
    )
    return load_client_config(p)


@pytest.fixture
def catalog():
    return {**load_catalog(REPO / "programs"), **load_catalog(REPO / "tests" / "fixtures" / "programs")}


@pytest.fixture
def statuses():
    return []


@pytest.fixture
def previews():
    return []


def _make(config, catalog, statuses, previews, **kw):
    async def on_status(s):
        statuses.append(s)

    async def on_preview(b):
        previews.append(b)

    return Supervisor(config, catalog, on_status, on_preview, **kw)


@pytest.fixture
async def sup(config, catalog, statuses, previews, monkeypatch):
    monkeypatch.setenv("SHELF_MATRIX_FAKE", "1")
    s = _make(config, catalog, statuses, previews)
    yield s
    await s.stop()


@pytest.fixture
async def sup_fast(config, catalog, statuses, previews, monkeypatch):
    monkeypatch.setenv("SHELF_MATRIX_FAKE", "1")
    s = _make(config, catalog, statuses, previews, backoff=(0.05,), max_crashes=3)
    yield s
    await s.stop()


async def wait_for(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def cmdline(pid):
    return Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")


def test_build_command_for_demo(config):
    m = load_manifest(REPO / "programs" / "demo-life")
    argv = build_command(m, {"step_ms": 100}, config, 70)
    assert argv[:4] == [f"{config.library_dir}/examples-api-use/demo", "-D7", "-m", "100"]
    assert argv[4:] == config.matrix.to_cli_flags(70)


def test_demo_manifests(catalog):
    assert catalog["demo-sandpile"].command[:4] == (
        "{library_dir}/examples-api-use/demo", "-D6", "-m", "{step_ms}")
    assert [(p.name, p.default) for p in catalog["demo-sandpile"].params] == [("step_ms", 50)]
    cube = catalog["demo-cube"]
    assert cube.command == ("{library_dir}/examples-api-use/demo", "-D12", "{matrix_flags}")
    assert cube.params == ()


async def test_start_python_program_reports_running(sup, statuses):
    await sup.start("solid", {"color": "#00ff00"}, {}, {})
    assert sup.current == "solid"
    await wait_for(lambda: statuses and statuses[-1]["state"] == "running")
    assert statuses[0] == {"active_program": "solid", "state": "starting", "detail": ""}
    assert statuses[-1]["active_program"] == "solid"


async def test_switch_stops_previous_child_first(sup, monkeypatch):
    await sup.start("solid", {}, {}, {})
    old = sup._proc
    seen = []
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kw):
        seen.append(old.returncode)
        return await real(*args, **kw)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    await sup.start("sleeper", {}, {}, {})
    assert seen == [old.returncode] and old.returncode is not None
    assert sup._proc.pid != old.pid and sup.current == "sleeper"


async def test_command_restarts_on_globals_change(sup, statuses):
    await sup.start("sleeper", {}, {"brightness": 50}, {})
    assert statuses[-1]["state"] == "running"
    first = sup._proc
    assert b"--led-brightness=50" in cmdline(first.pid)
    await sup.update_globals({"brightness": 20})
    assert first.returncode is not None
    assert sup._proc.pid != first.pid
    assert b"--led-brightness=20" in cmdline(sup._proc.pid)
    assert not any(s["state"] == "crashed" for s in statuses)


async def test_intentional_stop_is_not_a_crash(sup, statuses):
    await sup.start("solid", {}, {}, {})
    await wait_for(lambda: statuses[-1]["state"] == "running")
    proc = sup._proc
    await sup.stop()
    assert proc.returncode == 0 and sup.current is None
    await asyncio.sleep(0.1)
    assert not any(s["state"] == "crashed" for s in statuses)


async def test_crash_reports_detail_and_restarts(config, catalog, statuses, previews, monkeypatch):
    monkeypatch.setenv("SHELF_MATRIX_FAKE", "1")
    s = _make(config, catalog, statuses, previews, backoff=(0.05,), max_crashes=10)
    try:
        await s.start("crasher", {}, {}, {})
        await wait_for(lambda: sum(x["state"] == "crashed" for x in statuses) >= 2)
        crashed = next(x for x in statuses if x["state"] == "crashed")
        assert crashed["active_program"] == "crasher" and "exit code 1" in crashed["detail"]
        assert s.current == "crasher"
    finally:
        await s.stop()


async def test_crash_loop_falls_back_to_clock(sup_fast, statuses):
    await sup_fast.start("crasher", {}, {}, {})
    await wait_for(lambda: any(s["state"] == "fallback" for s in statuses))
    assert sup_fast.current == "clock"
    assert sum(s["state"] == "crashed" for s in statuses) == 3
    fb = next(s for s in statuses if s["state"] == "fallback")
    assert fb["active_program"] == "clock"


async def test_missing_binary_falls_back(sup_fast, statuses):
    await sup_fast.start("demo-life", {}, {}, {})
    await wait_for(lambda: any(s["state"] == "fallback" for s in statuses))
    assert sup_fast.current == "clock"
    crashed = next(s for s in statuses if s["state"] == "crashed")
    assert "FileNotFoundError" in crashed["detail"]


async def test_unknown_program_falls_back(sup, statuses):
    await sup.start("nope", {}, {}, {})
    assert sup.current == "clock"
    await wait_for(lambda: statuses[-1]["state"] == "fallback")
    assert "nope" in statuses[-1]["detail"]


async def test_preview_forwarded(sup, previews):
    await sup.start("solid", {}, {}, {})
    await sup.set_preview(True)
    await wait_for(lambda: previews, timeout=5)
    assert len(previews[0]) == 64 * 64 * 3


async def test_concurrent_starts_run_one_child(sup):
    await asyncio.gather(*(sup.start(p, {}, {}, {}) for p in ("solid", "sleeper", "solid", "sleeper")))
    assert sup.current == "sleeper"
    me = os.getpid()
    kids = [p for p in os.listdir("/proc") if p.isdigit() and _ppid(p) == me]
    assert kids == [str(sup._proc.pid)]


def _ppid(pid):
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return -1


def _spy_sends(sup):
    sent = []
    real = sup._send

    async def spy(msg):
        sent.append(msg)
        await real(msg)

    sup._send = spy
    return sent


async def test_live_globals_are_resolved_before_reaching_child(sup, statuses, previews):
    await sup.start("solid", {}, {"brightness": 40}, {})
    await wait_for(lambda: statuses[-1]["state"] == "running")
    sent = _spy_sends(sup)
    proc = sup._proc
    await sup.update_globals({"brightness": 500, "bogus": 1})
    assert sent == [{"kind": "globals", "globals": {"brightness": 70}}]
    await sup.update_params({"color": "red", "bogus": 1})
    assert sent[-1] == {"kind": "params", "params": {"color": "#ff0000"}}
    await sup.set_preview(True)
    await wait_for(lambda: previews, timeout=5)  # child still rendering
    assert sup._proc is proc and proc.returncode is None
    assert not any(s["state"] == "crashed" for s in statuses)


async def test_params_for_requested_program_do_not_leak_into_fallback(sup_fast, statuses):
    await sup_fast.start("demo-life", {"step_ms": 100}, {}, {})
    await wait_for(lambda: statuses[-1]["state"] == "fallback")
    assert sup_fast.current == "clock"
    sent = _spy_sends(sup_fast)
    proc = sup_fast._proc
    await sup_fast.update_params({"step_ms": 200, "color": "#000000"})
    assert not any(m["kind"] == "params" for m in sent)
    assert sup_fast._proc is proc  # clock not restarted or touched


async def test_unknown_program_without_fallback_clears_current(config, statuses, previews, monkeypatch):
    monkeypatch.setenv("SHELF_MATRIX_FAKE", "1")
    cat = load_catalog(REPO / "tests" / "fixtures" / "programs")
    s = _make(config, cat, statuses, previews)
    try:
        await s.start("sleeper", {}, {}, {})
        await s.start("nope", {}, {}, {})
        assert s.current is None and s._proc is None
        await s.update_globals({"brightness": 10})
        assert s._proc is None
        assert statuses[-1]["state"] == "crashed" and "not installed" in statuses[-1]["detail"]
    finally:
        await s.stop()


async def test_reader_error_does_not_break_stop(sup, statuses, monkeypatch, caplog):
    import shelf_matrix.client.supervisor as mod

    def boom(_):
        raise RuntimeError("reader boom")

    monkeypatch.setattr(mod, "json", type("J", (), {"loads": staticmethod(boom), "dumps": staticmethod(__import__("json").dumps)}))
    await sup.start("solid", {}, {}, {})
    proc = sup._proc
    await wait_for(lambda: "reading child stdout failed" in caplog.text)
    await sup.stop()
    assert proc.returncode is not None


async def test_cancelled_stop_still_reaps_child(config, catalog, statuses, previews, tmp_path):
    from shelf_matrix.schema import Manifest
    flag = tmp_path / "armed"
    catalog = {**catalog, "stubborn": Manifest(
        id="stubborn", name="S", kind="command", params=(),
        command=("{python}", "-c",
                 "import signal,time,sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                 "open(sys.argv[1],'w').close(); time.sleep(60)", str(flag)))}
    s = _make(config, catalog, statuses, previews, stop_timeout_s=0.5)
    try:
        await s.start("stubborn", {}, {}, {})
        proc = s._proc
        await wait_for(flag.exists)
        task = asyncio.create_task(s.stop())
        await asyncio.sleep(0.1)  # stop() is now waiting on the child
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await wait_for(lambda: proc.returncode is not None, timeout=3)
    finally:
        await s.stop()


def _inits(sent):
    return [m for m in sent if m["kind"] == "init"]


async def test_crash_loop_fallback_runs_with_saved_clock_params(sup_fast, statuses):
    sent = _spy_sends(sup_fast)
    await sup_fast.start("crasher", {}, {}, {},
                         fallback_params={"style": "analog", "color": "#00ff00"})
    await wait_for(lambda: statuses[-1]["state"] == "fallback")
    assert sup_fast.current == "clock"
    params = _inits(sent)[-1]["params"]
    assert params["style"] == "analog" and params["color"] == "#00ff00"


async def test_unknown_program_fallback_runs_with_saved_clock_params(sup, statuses):
    sent = _spy_sends(sup)
    await sup.start("nope", {}, {}, {}, fallback_params={"color": "#123456"})
    assert sup.current == "clock"
    assert _inits(sent)[-1]["params"]["color"] == "#123456"


async def test_clock_param_edit_during_fallback_reaches_child(sup_fast, statuses):
    await sup_fast.start("crasher", {}, {}, {}, fallback_params={"color": "#00ff00"})
    await wait_for(lambda: statuses[-1]["state"] == "fallback")
    sent = _spy_sends(sup_fast)
    proc = sup_fast._proc
    await sup_fast.update_fallback_params({"color": "#0000ff"})
    assert sent[-1]["kind"] == "params" and sent[-1]["params"]["color"] == "#0000ff"
    assert sup_fast._proc is proc


async def test_fallback_params_stored_while_not_on_fallback(sup, statuses):
    await sup.start("solid", {}, {}, {}, fallback_params={"color": "#00ff00"})
    await wait_for(lambda: statuses[-1]["state"] == "running")
    sent = _spy_sends(sup)
    await sup.update_fallback_params({"color": "#0000ff"})
    assert sent == []
    assert sup._params["clock"]["color"] == "#0000ff"
    assert sup._params["solid"] == {}
