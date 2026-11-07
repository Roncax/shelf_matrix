"""Frame loop shared by all Python programs."""
from __future__ import annotations

import base64
import json
import queue
import time
from datetime import datetime
from typing import Callable, TextIO

from PIL import Image

from shelf_matrix.program.base import Program, ProgramContext

PREVIEW_INTERVAL = 1.0


def _emit(out: TextIO, msg: dict) -> None:
    out.write(json.dumps(msg) + "\n")
    out.flush()


def _apply_brightness(matrix, ctx: ProgramContext) -> None:
    if "brightness" in ctx.globals:
        matrix.brightness = ctx.globals["brightness"]


def run_program(
    program: Program,
    matrix,
    ctx: ProgramContext,
    inbox: "queue.Queue[dict]",
    out: TextIO,
    max_frames: int | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> None:
    canvas = matrix.CreateFrameCanvas()
    _apply_brightness(matrix, ctx)
    preview = False
    last_preview = float("-inf")
    ready = False
    frames = 0
    pending: dict | None = None
    while max_frames is None or frames < max_frames:
        started = time.monotonic()
        changed = False
        while True:
            if pending is not None:
                msg, pending = pending, None
            else:
                try:
                    msg = inbox.get_nowait()
                except queue.Empty:
                    break
            kind = msg.get("kind")
            if kind == "eof":
                return
            if kind == "params":
                ctx.params.update(msg["params"])
                changed = True
            elif kind == "globals":
                ctx.globals.update(msg["globals"])
                _apply_brightness(matrix, ctx)
                changed = True
            elif kind == "data":
                ctx.data.setdefault(msg["topic"], {})[msg["key"]] = msg["value"]
                changed = True
            elif kind == "preview":
                preview = bool(msg["on"])
        if changed:
            program.on_update()

        img = Image.new("RGB", (ctx.width, ctx.height))
        program.draw(img, now())
        canvas.SetImage(img)
        canvas = matrix.SwapOnVSync(canvas)
        frames += 1

        if not ready:
            _emit(out, {"kind": "ready"})
            ready = True
        if preview and started - last_preview >= PREVIEW_INTERVAL:
            last_preview = started
            _emit(out, {"kind": "preview", "rgb": base64.b64encode(img.tobytes()).decode("ascii")})

        if max_frames is None or frames < max_frames:
            remaining = 1.0 / program.fps - (time.monotonic() - started)
            if remaining > 0:
                try:
                    pending = inbox.get(timeout=remaining)
                except queue.Empty:
                    pass
