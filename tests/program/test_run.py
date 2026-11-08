import io
import json
import os
import sys
import threading
from pathlib import Path

import pytest

from shelf_matrix.program import run
from shelf_matrix.program.backend import create_matrix as real_create_matrix

REPO = Path(__file__).resolve().parents[2]


class _Stdin:
    """Hands out the init line, then blocks until released (like an open pipe)."""

    def __init__(self, init: dict, release: threading.Event) -> None:
        self._init = json.dumps(init) + "\n"
        self._release = release

    def readline(self) -> str:
        return self._init

    def __iter__(self):
        self._release.wait(10)
        return iter(())


class _Stdout(io.StringIO):
    def __init__(self, ready: threading.Event) -> None:
        super().__init__()
        self._ready = ready

    def write(self, s: str) -> int:
        if '"ready"' in s:
            self._ready.set()
        return super().write(s)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_fonts_are_cached_before_matrix_drops_privileges(client_config_file, tmp_path, monkeypatch):
    """The real RGBMatrix drops root to `daemon`; after that the cache dir is read-only."""
    cache = tmp_path / "state" / "fontcache"
    fonts = sorted(p.stem for p in (REPO / "fonts").glob("*.bdf"))
    seen_at_create: list[str] = []

    def create_matrix(options, brightness):
        m = real_create_matrix(options, brightness)
        cache.mkdir(parents=True, exist_ok=True)
        seen_at_create.extend(sorted(p.stem for p in cache.glob("*.pil")))
        cache.chmod(0o555)
        return m

    ready = threading.Event()
    out = _Stdout(ready)
    monkeypatch.setenv("SHELF_MATRIX_FAKE", "1")
    monkeypatch.setattr(run, "create_matrix", create_matrix)
    monkeypatch.setattr(run.signal, "signal", lambda *a: None)
    monkeypatch.setattr(sys, "path", list(sys.path))
    for mod in ("program", "faces", "icons"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(sys, "stdin", _Stdin(
        {"kind": "init", "params": {}, "globals": {}, "data": {}, "preview": False}, ready))
    monkeypatch.setattr(sys, "stdout", out)
    try:
        assert run.main(["--config", str(client_config_file), str(REPO / "programs" / "clock")]) == 0
    finally:
        cache.chmod(0o755)
    assert ready.is_set(), out.getvalue()
    assert seen_at_create == fonts
