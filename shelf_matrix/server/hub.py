"""The server core: one Pi connection, many browsers, one persisted state.

Transport-agnostic: peers are anything with `send_text` / `send_bytes`. The
Pi treats `sync`, `set_params` and `set_globals` as full replacements, so the
hub always sends full resolved dicts; browser updates are partial and merged
into the stored values first. Bad input from either side is logged or
answered with `error`; it never raises out of the hub.

An active program missing from the Pi's current catalog is kept (still sent
in `sync` and reported by `state_payload`): the Pi runs its fallback on its
own, and the program resumes as soon as it reappears in a later catalog.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

from shelf_matrix.protocol import Message, ProtocolError, decode_preview, encode
from shelf_matrix.schema import GLOBAL_PARAMS, Manifest, ParamSpec, resolve_params, validate_value
from shelf_matrix.server.store import Store

logger = logging.getLogger(__name__)


class Peer(Protocol):
    async def send_text(self, text: str) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...


class _Rejected(Exception):
    """A browser request that is answered with `error` and not applied."""


def _parse_manifest(d: Any) -> Manifest | None:
    """A usable manifest, or None (logged) for anything malformed."""
    try:
        m = Manifest.from_dict(d)
        if not isinstance(m.id, str):
            raise ValueError("id must be a string")
        for spec in m.params:
            if not isinstance(spec.name, str):
                raise ValueError("param name must be a string")
            validate_value(spec, spec.default)
        return m
    except Exception as e:  # anything the Pi sends must not break the hub
        logger.warning("ignoring invalid manifest %r: %s", d, e)
        return None


def _validate(specs: tuple[ParamSpec, ...], values: Any) -> dict:
    """Validated copy of a partial update; raises _Rejected on any bad key."""
    if not isinstance(values, dict):
        raise _Rejected("expected an object of values")
    by_name = {s.name: s for s in specs}
    out = {}
    for name, value in values.items():
        spec = by_name.get(name)
        if spec is None:
            raise _Rejected(f"unknown parameter {name!r}")
        try:
            out[name] = validate_value(spec, value)
        except (ValueError, TypeError) as e:
            raise _Rejected(str(e)) from e
    return out


def _filter_valid(specs: tuple[ParamSpec, ...], values: Any) -> dict:
    """The subset of `values` that validates (used when adopting Pi state)."""
    if not isinstance(values, dict):
        return {}
    out = {}
    for spec in specs:
        if spec.name in values:
            try:
                out[spec.name] = validate_value(spec, values[spec.name])
            except (ValueError, TypeError):
                pass
    return out


class Hub:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.on_params_changed: list[Callable[[], Awaitable[None]]] = []
        self._client: Peer | None = None
        self._browsers: list[Peer] = []
        self._status: dict | None = None
        self._last_seen: datetime | None = None
        self._data: dict[tuple[str, str], Any] = {}
        self._manifests: dict[str, Manifest] = {}
        for d in store.catalog:
            m = _parse_manifest(d)
            if m is not None:
                self._manifests[m.id] = m

    # ----- state ------------------------------------------------------------

    def _resolved_params(self, program: str) -> dict:
        return resolve_params(self._manifests[program].params, self.store.params.get(program))

    def _resolved_globals(self) -> dict:
        return resolve_params(GLOBAL_PARAMS, self.store.globals)

    def state_payload(self) -> dict:
        return {
            "catalog": [m.to_dict() for m in self._manifests.values()],
            "active_program": self.store.active_program,
            "params": {pid: self._resolved_params(pid) for pid in self._manifests},
            "globals": self._resolved_globals(),
            "client": {
                "online": self._client is not None,
                "last_seen": self._last_seen.isoformat() if self._last_seen else None,
                "status": self._status,
            },
        }

    def _persist(self) -> None:
        try:
            self.store.save()
        except Exception as e:
            logger.warning("could not save store: %s", e)

    def _touch(self) -> None:
        self._last_seen = datetime.now(timezone.utc)

    # ----- sending ----------------------------------------------------------

    async def _send_client(self, kind: str, **payload) -> None:
        peer = self._client
        if peer is None:
            return
        try:
            await peer.send_text(encode(kind, **payload))
        except Exception as e:  # the transport closes and reports the drop
            logger.warning("send %r to client failed: %s", kind, e)

    async def _send_browser(self, peer: Peer, text: str | None = None, data: bytes | None = None) -> None:
        try:
            if text is not None:
                await peer.send_text(text)
            else:
                await peer.send_bytes(data)
        except Exception as e:
            logger.warning("dropping browser after failed send: %s", e)
            await self.browser_disconnected(peer)

    async def _broadcast_state(self) -> None:
        text = encode("state", **self.state_payload())
        for peer in list(self._browsers):
            await self._send_browser(peer, text=text)

    async def _error(self, peer: Peer, message: str) -> None:
        await self._send_browser(peer, text=encode("error", message=message))

    async def _notify_params_changed(self) -> None:
        for cb in list(self.on_params_changed):
            try:
                await cb()
            except Exception:
                logger.exception("on_params_changed callback failed")

    # ----- Pi side ----------------------------------------------------------

    async def client_connected(self, peer: Peer) -> None:
        self._client = peer
        self._touch()
        await self._broadcast_state()

    async def client_disconnected(self, peer: Peer) -> None:
        if peer is not self._client:  # an older, already replaced connection
            return
        self._client = None
        self._touch()
        await self._broadcast_state()

    async def on_client_message(self, msg: Message) -> None:
        try:
            self._touch()
            if msg.kind == "hello":
                await self._on_hello(msg.payload)
            elif msg.kind == "status":
                self._status = dict(msg.payload)
                await self._broadcast_state()
            else:
                logger.debug("ignoring client %r message", msg.kind)
        except Exception:
            logger.exception("error handling client %r message", msg.kind)

    async def _on_hello(self, p: dict) -> None:
        catalog = p.get("catalog")
        if not isinstance(catalog, list):
            logger.warning("ignoring hello without a catalog list")
            return
        manifests: dict[str, Manifest] = {}
        for d in catalog:
            m = _parse_manifest(d)
            if m is not None:
                manifests[m.id] = m
        self._manifests = manifests
        store = self.store
        store.catalog = [m.to_dict() for m in manifests.values()]
        if store.active_program is None:
            self._adopt(p.get("state"))
        self._persist()
        logger.info("client %s connected; catalog: %s", p.get("client_version"),
                    ", ".join(manifests) or "(empty)")

        if isinstance(store.active_program, str):
            await self._send_client(
                "sync",
                active_program=store.active_program,
                params={pid: self._resolved_params(pid) for pid in manifests},
                globals=self._resolved_globals(),
            )
        # The Pi turns preview off when the link drops; re-enable it if watched.
        if self._browsers:
            await self._send_client("preview_on")
        for (topic, key), value in self._data.items():
            await self._send_client("data", topic=topic, key=key, value=value)
        await self._broadcast_state()
        await self._notify_params_changed()

    def _adopt(self, state: Any) -> None:
        """First contact: take over the Pi's cached state (valid parts only)."""
        if not isinstance(state, dict):
            return
        store = self.store
        program = state.get("active_program")
        if isinstance(program, str):
            store.active_program = program
        params = state.get("params")
        if isinstance(params, dict):
            store.params = {
                pid: valid for pid, m in self._manifests.items()
                if (valid := _filter_valid(m.params, params.get(pid)))
            }
        store.globals = _filter_valid(GLOBAL_PARAMS, state.get("globals"))

    async def on_client_preview(self, data: bytes) -> None:
        try:
            decode_preview(data)
        except ProtocolError:
            logger.debug("dropping invalid preview frame (%d bytes)", len(data))
            return
        for peer in list(self._browsers):
            await self._send_browser(peer, data=data)

    async def publish_data(self, topic: str, key: str, value: Any) -> None:
        self._data[(topic, key)] = value
        await self._send_client("data", topic=topic, key=key, value=value)

    # ----- browser side -----------------------------------------------------

    async def browser_connected(self, peer: Peer) -> None:
        # Decide "first" before any await so concurrent connects can't both miss it.
        first = not self._browsers
        self._browsers.append(peer)
        if first:
            await self._send_client("preview_on")
        await self._send_browser(peer, text=encode("state", **self.state_payload()))

    async def browser_disconnected(self, peer: Peer) -> None:
        if peer not in self._browsers:
            return
        self._browsers.remove(peer)
        if not self._browsers:
            await self._send_client("preview_off")

    async def on_browser_message(self, peer: Peer, msg: Message) -> None:
        try:
            handler = getattr(self, f"_browser_{msg.kind}", None)
            if handler is None:
                raise _Rejected(f"unknown message {msg.kind!r}")
            await handler(msg.payload)
        except _Rejected as e:
            await self._error(peer, str(e))
        except Exception:
            logger.exception("error handling browser %r message", msg.kind)
            await self._error(peer, "internal error")

    def _program(self, p: dict) -> Manifest:
        program = p.get("program")
        m = self._manifests.get(program) if isinstance(program, str) else None
        if m is None:
            raise _Rejected(f"unknown program {program!r}")
        return m

    async def _browser_select_program(self, p: dict) -> None:
        m = self._program(p)
        self.store.active_program = m.id
        self._persist()
        await self._send_client("set_program", program=m.id, params=self._resolved_params(m.id))
        await self._broadcast_state()

    async def _browser_update_params(self, p: dict) -> None:
        m = self._program(p)
        values = _validate(m.params, p.get("params"))
        store = self.store
        store.params = {**store.params, m.id: {**store.params.get(m.id, {}), **values}}
        self._persist()
        await self._send_client("set_params", program=m.id, params=self._resolved_params(m.id))
        await self._broadcast_state()
        await self._notify_params_changed()

    async def _browser_update_globals(self, p: dict) -> None:
        values = _validate(GLOBAL_PARAMS, p.get("globals"))
        self.store.globals = {**self.store.globals, **values}
        self._persist()
        await self._send_client("set_globals", globals=self._resolved_globals())
        await self._broadcast_state()
