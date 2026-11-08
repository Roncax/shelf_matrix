"""Wire protocol shared by the Pi client, the server and the browser UI."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

PROTOCOL_VERSION = 1
PREVIEW_SIZE = 64
PREVIEW_BYTES = PREVIEW_SIZE * PREVIEW_SIZE * 3
_PREVIEW_PREFIX = b"\x01"


class ProtocolError(ValueError):
    pass


@dataclass
class Message:
    kind: str
    payload: dict = field(default_factory=dict)


def encode(kind: str, **payload) -> str:
    return json.dumps({"v": PROTOCOL_VERSION, "kind": kind, **payload})


def decode(text: str | bytes) -> Message:
    try:
        obj = json.loads(text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("message must be a JSON object")
    if obj.get("v") != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version: {obj.get('v')!r}")
    kind = obj.get("kind")
    if not isinstance(kind, str):
        raise ProtocolError("missing message kind")
    payload = {k: v for k, v in obj.items() if k not in ("v", "kind")}
    return Message(kind, payload)


def encode_preview(rgb: bytes) -> bytes:
    if len(rgb) != PREVIEW_BYTES:
        raise ValueError(f"preview must be {PREVIEW_BYTES} bytes, got {len(rgb)}")
    return _PREVIEW_PREFIX + bytes(rgb)


def decode_preview(data: bytes) -> bytes:
    if len(data) != PREVIEW_BYTES + 1 or data[:1] != _PREVIEW_PREFIX:
        raise ProtocolError("invalid preview frame")
    return bytes(data[1:])
