import base64
import io
import json
import os
import signal
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from shelf_matrix.program.backend import FakeMatrix
from shelf_matrix.program.runtime import run_program
from program import Solid

REPO = Path(__file__).resolve().parents[2]


def lines(out):
    return [json.loads(x) for x in out.getvalue().splitlines()]


def test_run_program_applies_params_live(make_ctx):
    m = FakeMatrix(64, 64)
    ctx = make_ctx(params={"color": "#ff0000"})
    inbox = queue.Queue(); out = io.StringIO()
    inbox.put({"kind": "params", "params": {"color": "#00ff00"}})
    run_program(Solid(ctx), m, ctx, inbox, out, max_frames=2)
    assert m.last_image.getpixel((10, 10)) == (0, 255, 0)


def test_run_program_sets_brightness(make_ctx):
    m = FakeMatrix(64, 64)
    ctx = make_ctx(params={"color": "#ff0000"}, globals={"brightness": 70})
    inbox = queue.Queue()
    run_program(Solid(ctx), m, ctx, inbox, io.StringIO(), max_frames=1)
    assert m.brightness == 70
    inbox.put({"kind": "globals", "globals": {"brightness": 30}})
    run_program(Solid(ctx), m, ctx, inbox, io.StringIO(), max_frames=1)
    assert m.brightness == 30


def test_data_and_on_update(make_ctx):
    m = FakeMatrix(64, 64)
    ctx = make_ctx(params={"color": "#ff0000"})
    calls = []
    prog = Solid(ctx)
    prog.on_update = lambda: calls.append(1)
    inbox = queue.Queue()
    inbox.put({"kind": "data", "topic": "t", "key": "k", "value": 5})
    run_program(prog, m, ctx, inbox, io.StringIO(), max_frames=1)
    assert ctx.data == {"t": {"k": 5}} and calls == [1]


def test_eof_ends_loop(make_ctx):
    ctx = make_ctx(params={"color": "#ff0000"})
    inbox = queue.Queue(); inbox.put({"kind": "eof"})
    run_program(Solid(ctx), FakeMatrix(64, 64), ctx, inbox, io.StringIO())


def test_preview_emitted_only_when_on(make_ctx):
    m = FakeMatrix(64, 64)
    ctx = make_ctx(params={"color": "#ff0000"})
    inbox = queue.Queue(); out = io.StringIO()
    run_program(Solid(ctx), m, ctx, inbox, out, max_frames=2)
    ks = [x["kind"] for x in lines(out)]
    assert ks == ["ready"]
    out = io.StringIO()
    inbox.put({"kind": "preview", "on": True})
    run_program(Solid(ctx), m, ctx, inbox, out, max_frames=2)
    prev = [x for x in lines(out) if x["kind"] == "preview"]
    assert len(prev) == 1
    raw = base64.b64decode(prev[0]["rgb"])
    assert len(raw) == 12288 and tuple(raw[:3]) == (255, 0, 0)


def test_child_process_end_to_end(client_config_file):
    env = {**os.environ, "SHELF_MATRIX_FAKE": "1"}
    p = subprocess.Popen(
        [sys.executable, "-m", "shelf_matrix.program.run", "--config", str(client_config_file),
         str(REPO / "tests/fixtures/programs/solid")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=REPO,
    )
    try:
        p.stdin.write(json.dumps({"kind": "init", "params": {}, "globals": {},
                                  "data": {}, "preview": True}) + "\n")
        p.stdin.flush()
        seen = set()
        deadline = time.time() + 10

        def read():
            for line in p.stdout:
                seen.add(json.loads(line)["kind"])
                if {"ready", "preview"} <= seen:
                    return
        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(max(0.1, deadline - time.time()))
        assert {"ready", "preview"} <= seen, p.stderr.read() if p.poll() is not None else seen
        p.stdin.close()
        assert p.wait(timeout=5) == 0
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
        p.stdout.close()
        p.stderr.close()


def test_params_and_eof_interrupt_slow_fps(make_ctx):
    m = FakeMatrix(64, 64)
    ctx = make_ctx(params={"color": "#ff0000"})
    prog = Solid(ctx)
    prog.fps = 0.2
    inbox = queue.Queue()
    t = threading.Thread(target=run_program, args=(prog, m, ctx, inbox, io.StringIO()))
    t.start()
    time.sleep(0.2)
    assert m.last_image.getpixel((1, 1)) == (255, 0, 0)
    inbox.put({"kind": "params", "params": {"color": "#00ff00"}})
    time.sleep(0.5)
    assert m.last_image.getpixel((1, 1)) == (0, 255, 0)
    inbox.put({"kind": "eof"})
    t.join(1)
    assert not t.is_alive()


def test_child_exits_cleanly_on_sigterm(client_config_file):
    p = subprocess.Popen(
        [sys.executable, "-m", "shelf_matrix.program.run", "--config", str(client_config_file),
         str(REPO / "tests/fixtures/programs/solid")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={**os.environ, "SHELF_MATRIX_FAKE": "1"}, cwd=REPO,
    )
    try:
        p.stdin.write(json.dumps({"kind": "init", "params": {}, "globals": {},
                                  "data": {}, "preview": False}) + "\n")
        p.stdin.flush()
        assert json.loads(p.stdout.readline())["kind"] == "ready"
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=5) == 0
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
        p.stdin.close()
        p.stdout.close()
        p.stderr.close()
