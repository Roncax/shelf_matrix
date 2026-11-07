"""Owns the single program child process that drives the matrix."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import signal
import sys
import time
from collections import deque
from typing import Any, Awaitable, Callable

from shelf_matrix.config import ClientConfig
from shelf_matrix.schema import GLOBAL_PARAMS, Manifest, resolve_params

logger = logging.getLogger(__name__)

MATRIX_FLAGS = "{matrix_flags}"
READ_LIMIT = 1 << 20  # max child stdout line; a preview line is ~16 KiB


def build_command(manifest: Manifest, params: dict, config: ClientConfig, brightness: int) -> list[str]:
    fmt = {**params, "library_dir": str(config.library_dir), "python": sys.executable}
    argv: list[str] = []
    for element in manifest.command or ():
        if element == MATRIX_FLAGS:
            argv.extend(config.matrix.to_cli_flags(brightness))
        else:
            argv.append(element.format(**fmt))
    return argv


def _describe_exit(rc: int) -> str:
    if rc < 0:
        try:
            return f"killed by signal {signal.Signals(-rc).name}"
        except ValueError:
            return f"killed by signal {-rc}"
    return f"exit code {rc}"


class Supervisor:
    def __init__(
        self,
        config: ClientConfig,
        catalog: dict[str, Manifest],
        on_status: Callable[[dict], Awaitable[None]],
        on_preview: Callable[[bytes], Awaitable[None]],
        *,
        backoff: tuple[float, ...] = (1, 2, 4, 8, 16, 30),
        crash_window_s: float = 60.0,
        max_crashes: int = 5,
        stop_timeout_s: float = 5.0,
    ) -> None:
        self._config = config
        self._catalog = catalog
        self._on_status = on_status
        self._on_preview = on_preview
        self._backoff = backoff
        self._crash_window_s = crash_window_s
        self._max_crashes = max_crashes
        self._stop_timeout_s = stop_timeout_s

        # Serializes every operation touching the child: one process at a time.
        self._lock = asyncio.Lock()
        self._manifest: Manifest | None = None
        # The program the caller asked for; differs from _manifest.id while on
        # the fallback. Params are stored per program id so params meant for
        # the requested program never leak into the fallback.
        self._requested: str | None = None
        self._params: dict[str, dict] = {}
        self._globals: dict = {}
        self._data: dict = {}
        self._preview = False
        self._fallback_reason: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._watcher: asyncio.Task | None = None
        self._retry: asyncio.Task | None = None
        self._reap: asyncio.Task | None = None
        # Bumped on every spawn and every intentional stop; a watcher or retry
        # whose generation is stale belongs to a child we stopped on purpose.
        self._gen = 0
        self._crashes: deque[float] = deque()

    @property
    def current(self) -> str | None:
        return self._manifest.id if self._manifest else None

    # ----- public API -------------------------------------------------------

    async def start(self, program_id: str, params: dict, globals: dict, data: dict) -> None:
        async with self._lock:
            await self._stop_locked()
            self._crashes.clear()
            self._globals = dict(globals)
            self._data = {topic: dict(values) for topic, values in data.items()}
            self._requested = program_id
            self._params = {program_id: dict(params)}
            manifest = self._catalog.get(program_id)
            if manifest is None:
                self._fallback_reason = f"unknown program {program_id!r}"
                manifest = self._catalog.get(self._config.fallback_program)
                if manifest is None:
                    self._manifest = None
                    await self._emit_missing_fallback()
                    return
            else:
                self._fallback_reason = None
            self._manifest = manifest
            await self._spawn_locked()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()
            self._manifest = None
            self._requested = None
            self._fallback_reason = None

    async def update_params(self, params: dict) -> None:
        async with self._lock:
            if self._requested is None:
                return
            self._params.setdefault(self._requested, {}).update(params)
            if self._manifest is not None and self._manifest.id == self._requested:
                await self._update_locked({"kind": "params", "params": self._resolved_params()})

    async def update_globals(self, globals: dict) -> None:
        async with self._lock:
            self._globals.update(globals)
            await self._update_locked({"kind": "globals", "globals": self._resolved_globals()})

    async def push_data(self, topic: str, key: str, value: Any) -> None:
        async with self._lock:
            self._data.setdefault(topic, {})[key] = value
            if self._is_python():
                await self._send({"kind": "data", "topic": topic, "key": key, "value": value})

    async def set_preview(self, on: bool) -> None:
        async with self._lock:
            self._preview = bool(on)
            if self._is_python():
                await self._send({"kind": "preview", "on": self._preview})

    # ----- internals (call with the lock held) ------------------------------

    def _resolved_params(self) -> dict:
        assert self._manifest is not None
        return resolve_params(self._manifest.params, self._params.get(self._manifest.id))

    def _resolved_globals(self) -> dict:
        return resolve_params(GLOBAL_PARAMS, self._globals)

    def _is_python(self) -> bool:
        return self._manifest is not None and self._manifest.kind == "python"

    async def _update_locked(self, msg: dict) -> None:
        if self._manifest is None:
            return
        if self._is_python():
            await self._send(msg)
        else:
            await self._stop_locked()
            await self._spawn_locked()

    async def _send(self, msg: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            return
        try:
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass  # child is exiting; the watcher reports it

    async def _spawn_locked(self) -> None:
        manifest = self._manifest
        assert manifest is not None
        self._gen += 1
        gen = self._gen
        params = self._resolved_params()
        globals_ = self._resolved_globals()
        await self._status("starting")
        try:
            if manifest.kind == "python":
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "shelf_matrix.program.run",
                    "--config", str(self._config.path), str(manifest.path),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    limit=READ_LIMIT,
                )
            else:
                argv = build_command(manifest, params, self._config, globals_["brightness"])
                proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL)
        except (OSError, ValueError, KeyError, IndexError) as e:
            await self._on_crash(gen, f"spawn failed: {type(e).__name__}: {e}")
            return
        self._proc = proc
        if manifest.kind == "python":
            self._reader = asyncio.create_task(self._read_stdout(proc, gen))
            await self._send({
                "kind": "init", "params": params, "globals": globals_,
                "data": self._data, "preview": self._preview,
            })
        self._watcher = asyncio.create_task(self._watch(proc, gen))
        if manifest.kind == "command":
            await self._status_up()

    async def _stop_locked(self) -> None:
        self._gen += 1
        if self._retry is not None:
            self._retry.cancel()
            self._retry = None
        proc = self._proc
        if proc is not None:
            # Shielded: even if our caller is cancelled, the child is still
            # terminated and reaped, and _proc keeps tracking it until then.
            if self._reap is None or self._reap.done():
                self._reap = asyncio.create_task(self._terminate(proc))
            await asyncio.shield(self._reap)
            self._proc = None
        me = asyncio.current_task()
        for task in (self._reader, self._watcher):
            if task is not None and task is not me:
                try:
                    await task
                except Exception:
                    logger.exception("child task failed")
        self._reader = self._watcher = None

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stdin is not None:
            proc.stdin.close()
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), self._stop_timeout_s)
        except asyncio.TimeoutError:
            logger.warning("child %s ignored SIGTERM; killing", proc.pid)
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()

    # ----- background tasks -------------------------------------------------

    async def _read_stdout(self, proc: asyncio.subprocess.Process, gen: int) -> None:
        try:
            await self._read_lines(proc, gen)
        except Exception:
            logger.exception("reading child stdout failed")
            # Keep draining so the child never blocks on a full pipe.
            assert proc.stdout is not None
            try:
                while await proc.stdout.read(65536):
                    pass
            except Exception:
                logger.exception("draining child stdout failed")

    async def _read_lines(self, proc: asyncio.subprocess.Process, gen: int) -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                logger.warning("bad line from child: %r", raw[:100])
                continue
            if proc is not self._proc or gen != self._gen:
                continue  # stopped or crashed; don't report a stale "ready"
            kind = msg.get("kind") if isinstance(msg, dict) else None
            if kind == "ready":
                await self._status_up()
            elif kind == "preview":
                try:
                    rgb = base64.b64decode(msg["rgb"])
                    await self._on_preview(rgb)
                except Exception:
                    logger.exception("preview forwarding failed")

    async def _watch(self, proc: asyncio.subprocess.Process, gen: int) -> None:
        rc = await proc.wait()
        if gen != self._gen:
            return  # intentional stop
        self._proc = None
        await self._on_crash(gen, _describe_exit(rc))

    async def _on_crash(self, gen: int, why: str) -> None:
        if gen != self._gen or self._manifest is None:
            return
        now = time.monotonic()
        self._crashes.append(now)
        while self._crashes and now - self._crashes[0] > self._crash_window_s:
            self._crashes.popleft()
        fallback = self._catalog.get(self._config.fallback_program)
        if self._fallback_reason is not None:
            delay = self._backoff[-1]  # fallback itself crash-looping
            target = self._manifest
        elif len(self._crashes) >= self._max_crashes and fallback is not None:
            await self._status("crashed", why)
            self._retry = asyncio.create_task(self._restart_after(
                gen, 0, fallback,
                f"{self._manifest.id} crashed {len(self._crashes)} times "
                f"in {self._crash_window_s:g}s ({why})"))
            return
        else:
            delay = self._backoff[min(len(self._crashes), len(self._backoff)) - 1]
            target = self._manifest
        await self._status("crashed", f"{why}; restarting in {delay:g}s")
        self._retry = asyncio.create_task(self._restart_after(gen, delay, target, self._fallback_reason))

    async def _restart_after(self, gen: int, delay: float, manifest: Manifest, reason: str | None) -> None:
        await asyncio.sleep(delay)
        async with self._lock:
            if gen != self._gen:
                return
            self._retry = None
            await self._stop_locked()  # reap the crashed child's tasks
            if manifest is not self._manifest:
                self._manifest = manifest
                self._crashes.clear()
            self._fallback_reason = reason
            await self._spawn_locked()

    # ----- status -----------------------------------------------------------

    async def _status_up(self) -> None:
        if self._fallback_reason is not None:
            await self._status("fallback", self._fallback_reason)
        else:
            await self._status("running")

    async def _status(self, state: str, detail: str = "") -> None:
        assert self._manifest is not None
        await self._emit({"active_program": self._manifest.id, "state": state, "detail": detail})

    async def _emit_missing_fallback(self) -> None:
        await self._emit({
            "active_program": self._config.fallback_program, "state": "crashed",
            "detail": f"{self._fallback_reason}; fallback program "
                      f"{self._config.fallback_program!r} not installed",
        })

    async def _emit(self, status: dict) -> None:
        try:
            await self._on_status(status)
        except Exception:
            logger.exception("on_status callback failed")
