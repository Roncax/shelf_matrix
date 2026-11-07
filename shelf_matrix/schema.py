"""Program manifests and parameter validation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

PARAM_TYPES = {"color", "choice", "bool", "int", "float", "text"}
KINDS = {"python", "command"}
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    type: str
    default: Any
    options: tuple[str, ...] = ()
    min: float | None = None
    max: float | None = None
    step: float | None = None
    max_len: int = 200


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    kind: str
    params: tuple[ParamSpec, ...]
    entry: str | None = None
    command: tuple[str, ...] | None = None
    needs: tuple[str, ...] = ()
    path: Path | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "params": [
                {
                    "name": p.name, "label": p.label, "type": p.type,
                    "default": p.default, "options": list(p.options),
                    "min": p.min, "max": p.max, "step": p.step,
                    "max_len": p.max_len,
                }
                for p in self.params
            ],
            "entry": self.entry,
            "command": list(self.command) if self.command is not None else None,
            "needs": list(self.needs),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        params = tuple(
            ParamSpec(
                name=p["name"], label=p["label"], type=p["type"],
                default=p["default"], options=tuple(p.get("options", ())),
                min=p.get("min"), max=p.get("max"), step=p.get("step"),
                max_len=p.get("max_len", 200),
            )
            for p in d.get("params", ())
        )
        command = d.get("command")
        return cls(
            id=d["id"], name=d["name"], kind=d["kind"], params=params,
            entry=d.get("entry"),
            command=tuple(command) if command is not None else None,
            needs=tuple(d.get("needs", ())),
        )


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_range(spec: ParamSpec, v: float) -> None:
    if spec.min is not None and v < spec.min:
        raise ValueError(f"{spec.name}: {v} below minimum {spec.min}")
    if spec.max is not None and v > spec.max:
        raise ValueError(f"{spec.name}: {v} above maximum {spec.max}")


def validate_value(spec: ParamSpec, value: Any) -> Any:
    t = spec.type
    if t == "color":
        if not isinstance(value, str) or not _COLOR_RE.match(value):
            raise ValueError(f"{spec.name}: invalid color {value!r}")
        return value.lower()
    if t == "choice":
        if value not in spec.options:
            raise ValueError(f"{spec.name}: {value!r} not in {spec.options}")
        return value
    if t == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{spec.name}: expected bool")
        return value
    if t == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{spec.name}: expected int")
        _check_range(spec, value)
        return value
    if t == "float":
        if not _is_number(value):
            raise ValueError(f"{spec.name}: expected number")
        _check_range(spec, value)
        return float(value)
    if t == "text":
        if not isinstance(value, str):
            raise ValueError(f"{spec.name}: expected string")
        if len(value) > spec.max_len:
            raise ValueError(f"{spec.name}: longer than {spec.max_len}")
        return value
    raise ValueError(f"{spec.name}: unknown type {t!r}")


def resolve_params(specs: Iterable[ParamSpec], stored: Mapping[str, Any] | None) -> dict[str, Any]:
    stored = stored or {}
    out: dict[str, Any] = {}
    for spec in specs:
        if spec.name in stored:
            try:
                out[spec.name] = validate_value(spec, stored[spec.name])
                continue
            except ValueError:
                pass
        out[spec.name] = spec.default
    return out


GLOBAL_PARAMS: tuple[ParamSpec, ...] = (
    ParamSpec("brightness", "Brightness", "int", 70, min=1, max=100, step=1),
)


def _parse_param(raw: Any) -> ParamSpec:
    if not isinstance(raw, dict):
        raise ManifestError("param entry must be a table")
    for key in ("name", "label", "type", "default"):
        if key not in raw:
            raise ManifestError(f"param missing key {key!r}")
    if raw["type"] not in PARAM_TYPES:
        raise ManifestError(f"param {raw['name']!r}: unknown type {raw['type']!r}")
    spec = ParamSpec(
        name=raw["name"], label=raw["label"], type=raw["type"],
        default=raw["default"], options=tuple(raw.get("options", ())),
        min=raw.get("min"), max=raw.get("max"), step=raw.get("step"),
        max_len=raw.get("max_len", 200),
    )
    try:
        validate_value(spec, spec.default)
    except ValueError as e:
        raise ManifestError(f"invalid default: {e}") from e
    return spec


def load_manifest(path: Path) -> Manifest:
    path = Path(path)
    try:
        with open(path / "manifest.toml", "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ManifestError(f"cannot read manifest in {path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f"invalid TOML in {path}: {e}") from e
    for key in ("id", "name", "kind"):
        if key not in data:
            raise ManifestError(f"manifest missing key {key!r}")
    kind = data["kind"]
    if kind not in KINDS:
        raise ManifestError(f"unknown kind {kind!r}")
    entry = data.get("entry")
    command = data.get("command")
    if kind == "python" and not entry:
        raise ManifestError("python manifest requires 'entry'")
    if kind == "command" and not command:
        raise ManifestError("command manifest requires 'command'")
    return Manifest(
        id=data["id"], name=data["name"], kind=kind,
        params=tuple(_parse_param(p) for p in data.get("params", ())),
        entry=entry,
        command=tuple(command) if command else None,
        needs=tuple(data.get("needs", ())),
        path=path,
    )
