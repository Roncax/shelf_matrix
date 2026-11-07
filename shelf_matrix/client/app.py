"""The Pi client process: cached program at boot, then the server link."""
from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version

from shelf_matrix.client.catalog import load_catalog
from shelf_matrix.client.link import ServerLink
from shelf_matrix.client.state import LocalState, load_state, save_state
from shelf_matrix.client.supervisor import Supervisor
from shelf_matrix.config import ClientConfig
from shelf_matrix.protocol import Message, encode_preview
from shelf_matrix.schema import GLOBAL_PARAMS, resolve_params

logger = logging.getLogger(__name__)

try:
    CLIENT_VERSION = version("shelf-matrix")
except PackageNotFoundError:  # running from a source tree
    CLIENT_VERSION = "unknown"


def _is_str_dict(v) -> bool:
    return isinstance(v, dict) and all(isinstance(k, str) for k in v)


class ClientApp:
    """Runs the cached program, then follows the server.

    State contract: the server always sends full dicts, so `sync` replaces all
    params and globals, `set_params` replaces one program's params and
    `set_globals` replaces the globals. Whatever is pushed to the supervisor is
    the full resolved dict for the stored state, so the running child always
    matches what is persisted (a key the server dropped falls back to its
    default instead of lingering).
    """

    def __init__(self, config: ClientConfig, *, link_backoff: tuple[float, ...] | None = None) -> None:
        self.config = config
        self._link_backoff = link_backoff
        self.state = LocalState()
        self.catalog = {}
        self.supervisor: Supervisor | None = None
        self.link: ServerLink | None = None
        self._last_status: dict | None = None

    async def run(self) -> None:
        cfg = self.config
        self.state = load_state(cfg.state_path)
        self.catalog = load_catalog(cfg.programs_dir)
        logger.info("catalog: %s", ", ".join(self.catalog) or "(empty)")
        self.supervisor = Supervisor(cfg, self.catalog, self._on_status, self._on_preview)
        kw = {} if self._link_backoff is None else {"backoff": self._link_backoff}
        self.link = ServerLink(cfg.server_url, cfg.token, self._on_connect, self._on_message,
                               on_disconnect=self._on_disconnect, **kw)
        try:
            # Show the cached program before (and regardless of) the server.
            await self._start_active()
            await self.link.run()
        finally:
            await self.supervisor.stop()  # child clears the matrix on exit

    # ----- supervisor callbacks: must never block ---------------------------

    async def _on_status(self, status: dict) -> None:
        self._last_status = status
        if self.link is not None:
            await self.link.send_json("status", **status)

    async def _on_preview(self, rgb: bytes) -> None:
        if self.link is not None and self.link.connected:
            await self.link.send_bytes(encode_preview(rgb))

    # ----- link callbacks ---------------------------------------------------

    async def _on_connect(self) -> None:
        s = self.state
        await self.link.send_json(
            "hello",
            client_version=CLIENT_VERSION,
            catalog=[m.to_dict() for m in self.catalog.values()],
            state={"active_program": s.active_program, "params": s.params, "globals": s.globals},
        )
        if self._last_status is not None:
            await self.link.send_json("status", **self._last_status)

    async def _on_disconnect(self) -> None:
        # Nobody is watching; the server re-sends preview_on after the next sync.
        await self.supervisor.set_preview(False)

    async def _on_message(self, msg: Message) -> None:
        handler = getattr(self, f"_handle_{msg.kind}", None)
        if handler is None:
            logger.debug("ignoring %r message", msg.kind)
            return
        await handler(msg.payload)

    async def _handle_sync(self, p: dict) -> None:
        program, params, globals_ = p.get("active_program"), p.get("params"), p.get("globals")
        if not (isinstance(program, str) and _is_str_dict(params) and _is_str_dict(globals_)):
            logger.warning("ignoring malformed sync: %r", p)
            return
        s = self.state
        changed = program != s.active_program
        s.active_program = program
        s.params = {k: dict(v) for k, v in params.items() if _is_str_dict(v)}
        s.globals = dict(globals_)
        self._persist()
        if changed:
            await self._start_active()
        else:
            await self.supervisor.update_params(self._resolved_params(program))
            await self.supervisor.update_globals(self._resolved_globals())

    async def _handle_set_program(self, p: dict) -> None:
        program, params = p.get("program"), p.get("params", None)
        if not isinstance(program, str) or not (params is None or _is_str_dict(params)):
            logger.warning("ignoring malformed set_program: %r", p)
            return
        s = self.state
        if params is not None:
            s.params[program] = dict(params)
        s.active_program = program
        self._persist()
        await self._start_active()

    async def _handle_set_params(self, p: dict) -> None:
        program, params = p.get("program"), p.get("params")
        if not (isinstance(program, str) and _is_str_dict(params)):
            logger.warning("ignoring malformed set_params: %r", p)
            return
        self.state.params[program] = dict(params)
        self._persist()
        if program == self.state.active_program:
            await self.supervisor.update_params(self._resolved_params(program))

    async def _handle_set_globals(self, p: dict) -> None:
        globals_ = p.get("globals")
        if not _is_str_dict(globals_):
            logger.warning("ignoring malformed set_globals: %r", p)
            return
        self.state.globals = dict(globals_)
        self._persist()
        await self.supervisor.update_globals(self._resolved_globals())

    async def _handle_data(self, p: dict) -> None:
        topic, key = p.get("topic"), p.get("key")
        if not (isinstance(topic, str) and isinstance(key, str)) or "value" not in p:
            logger.warning("ignoring malformed data: %r", p)
            return
        self.state.data.setdefault(topic, {})[key] = p["value"]
        self._persist()
        await self.supervisor.push_data(topic, key, p["value"])

    async def _handle_preview_on(self, p: dict) -> None:
        await self.supervisor.set_preview(True)

    async def _handle_preview_off(self, p: dict) -> None:
        await self.supervisor.set_preview(False)

    # ----- helpers ----------------------------------------------------------

    async def _start_active(self) -> None:
        s = self.state
        await self.supervisor.start(
            s.active_program, self._resolved_params(s.active_program),
            self._resolved_globals(), s.data)

    def _resolved_params(self, program: str) -> dict:
        manifest = self.catalog.get(program)
        stored = self.state.params.get(program)
        if manifest is None:  # unknown here; the supervisor runs the fallback
            return dict(stored or {})
        return resolve_params(manifest.params, stored)

    def _resolved_globals(self) -> dict:
        return resolve_params(GLOBAL_PARAMS, self.state.globals)

    def _persist(self) -> None:
        try:
            save_state(self.config.state_path, self.state)
        except OSError as e:
            logger.warning("could not save state: %s", e)
