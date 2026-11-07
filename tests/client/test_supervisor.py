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
