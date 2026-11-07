"""Child process entry: python -m shelf_matrix.program.run --config CFG PROGRAM_DIR"""
from __future__ import annotations

import argparse
import importlib
import json
import queue
import signal
import sys
import threading
from pathlib import Path

from shelf_matrix.config import load_client_config
from shelf_matrix.program.backend import create_matrix
from shelf_matrix.program.base import ProgramContext
from shelf_matrix.program.fonts import FontCache
from shelf_matrix.program.runtime import run_program
from shelf_matrix.schema import GLOBAL_PARAMS, load_manifest, resolve_params


def _read_stdin(inbox: "queue.SimpleQueue[dict]") -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            inbox.put(json.loads(line))
        except json.JSONDecodeError:
            print(f"bad ipc line: {line[:100]}", file=sys.stderr)
    inbox.put({"kind": "eof"})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("program_dir", type=Path)
    args = ap.parse_args(argv)

    # SimpleQueue.put is reentrant, so it is safe to call from a signal handler.
    inbox: "queue.SimpleQueue[dict]" = queue.SimpleQueue()
    signal.signal(signal.SIGTERM, lambda *_: inbox.put({"kind": "eof"}))

    config = load_client_config(args.config)
    manifest = load_manifest(args.program_dir)
    sys.path.insert(0, str(args.program_dir.resolve()))
    module_name, _, class_name = (manifest.entry or "").partition(":")
    program_cls = getattr(importlib.import_module(module_name), class_name)

    init = json.loads(sys.stdin.readline())
    params = resolve_params(manifest.params, init.get("params"))
    globals_ = resolve_params(GLOBAL_PARAMS, init.get("globals"))
    matrix = create_matrix(config.matrix, globals_["brightness"])
    ctx = ProgramContext(
        params=params, globals=globals_, data=init.get("data") or {},
        fonts=FontCache(config.fonts_dir, config.state_path.parent / "fontcache"),
        width=config.matrix.cols, height=config.matrix.rows,
    )
    inbox.put({"kind": "preview", "on": bool(init.get("preview"))})
    threading.Thread(target=_read_stdin, args=(inbox,), daemon=True).start()
    try:
        run_program(program_cls(ctx), matrix, ctx, inbox, sys.stdout)
    finally:
        if hasattr(matrix, "Clear"):
            matrix.Clear()
    return 0


if __name__ == "__main__":
    sys.exit(main())
