"""Local state cache; survives power loss (atomic write) and corruption."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LocalState:
    active_program: str = "clock"
    params: dict[str, dict] = field(default_factory=dict)
    globals: dict = field(default_factory=dict)
    data: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_state(path: Path) -> LocalState:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("state root must be an object")
        state = LocalState(
            active_program=raw.get("active_program", "clock"),
            params=raw.get("params", {}),
            globals=raw.get("globals", {}),
            data=raw.get("data", {}),
        )
        if (not isinstance(state.active_program, str)
                or not isinstance(state.params, dict)
                or not isinstance(state.globals, dict)
                or not isinstance(state.data, dict)):
            raise ValueError("state fields have wrong types")
        return state
    except FileNotFoundError:
        return LocalState()
    except (OSError, ValueError) as e:
        logger.warning("ignoring unreadable state %s: %s", path, e)
        return LocalState()


def save_state(path: Path, state: LocalState) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
