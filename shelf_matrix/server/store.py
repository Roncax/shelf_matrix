"""Server state persisted as one JSON document."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class Store:
    """`{"active_program", "params": {id: {...}}, "globals", "catalog"}` on disk.

    A missing or corrupt file starts empty; `save()` is atomic (tmp + fsync +
    replace) so a crash never leaves a half-written file.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._active_program: str | None = None
        self._params: dict[str, dict] = {}
        self._globals: dict = {}
        self._catalog: list[dict] = []
        self._load()

    @property
    def active_program(self) -> str | None:
        return self._active_program

    @active_program.setter
    def active_program(self, value: str | None) -> None:
        self._active_program = value

    @property
    def params(self) -> dict[str, dict]:
        return self._params

    @params.setter
    def params(self, value: dict[str, dict]) -> None:
        self._params = value

    @property
    def globals(self) -> dict:
        return self._globals

    @globals.setter
    def globals(self, value: dict) -> None:
        self._globals = value

    @property
    def catalog(self) -> list[dict]:
        return self._catalog

    @catalog.setter
    def catalog(self, value: list[dict]) -> None:
        self._catalog = value

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("store root must be an object")
            active = raw.get("active_program")
            params = raw.get("params", {})
            globals_ = raw.get("globals", {})
            catalog = raw.get("catalog", [])
            if (not (active is None or isinstance(active, str))
                    or not isinstance(params, dict)
                    or not all(isinstance(v, dict) for v in params.values())
                    or not isinstance(globals_, dict)
                    or not isinstance(catalog, list)
                    or not all(isinstance(m, dict) for m in catalog)):
                raise ValueError("store fields have wrong types")
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            logger.warning("ignoring unreadable store %s: %s", self.path, e)
            return
        self._active_program, self._params = active, params
        self._globals, self._catalog = globals_, catalog

    def save(self) -> None:
        doc = {
            "active_program": self._active_program,
            "params": self._params,
            "globals": self._globals,
            "catalog": self._catalog,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
