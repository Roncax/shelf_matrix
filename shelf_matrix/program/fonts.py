"""BDF font loading with a converted-.pil on-disk cache.

The cache must be filled (``warm()``) before the matrix is created: the real
rgbmatrix library drops root privileges to ``daemon`` and the cache directory
is root-owned, so after that point the cache is read-only. If a conversion is
still needed afterwards, it goes to a private temp directory instead.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PIL import BdfFontFile, ImageFont


class FontCache:
    def __init__(self, fonts_dir: Path, cache_dir: Path) -> None:
        self.fonts_dir = Path(fonts_dir)
        self.cache_dir = Path(cache_dir)
        self._loaded: dict[str, ImageFont.ImageFont] = {}
        self._fallback_dir: Path | None = None

    def names(self) -> list[str]:
        return sorted(p.stem for p in self.fonts_dir.glob("*.bdf"))

    def warm(self) -> None:
        """Convert every .bdf that has no cache entry yet (call while still root)."""
        for name in self.names():
            if not self._cached(self.cache_dir, name):
                try:
                    self._convert(self.cache_dir, name)
                except OSError as e:
                    print(f"font cache: cannot convert {name}: {e}", file=sys.stderr)

    def get(self, name: str) -> ImageFont.ImageFont:
        if name in self._loaded:
            return self._loaded[name]
        if name not in self.names():
            raise KeyError(name)
        font = self._load(name)
        self._loaded[name] = font
        return font

    @staticmethod
    def _cached(directory: Path, name: str) -> bool:
        return (directory / f"{name}.pil").exists() and (directory / f"{name}.pbm").exists()

    def _load(self, name: str) -> ImageFont.ImageFont:
        for directory in (self._fallback_dir, self.cache_dir):
            if directory is not None and self._cached(directory, name):
                try:
                    return ImageFont.load(str(directory / f"{name}.pil"))
                except Exception as e:  # corrupt/truncated entry: reconvert once
                    print(f"font cache: reconverting {name}: {e}", file=sys.stderr)
                break
        return ImageFont.load(str(self._convert_somewhere(name) / f"{name}.pil"))

    def _convert_somewhere(self, name: str) -> Path:
        try:
            self._convert(self.cache_dir, name)
            return self.cache_dir
        except OSError:
            if self._fallback_dir is None:
                self._fallback_dir = Path(tempfile.mkdtemp(prefix="shelf-matrix-fonts-"))
            self._convert(self._fallback_dir, name)
            return self._fallback_dir

    def _convert(self, directory: Path, name: str) -> None:
        """Write <name>.pbm and <name>.pil atomically (temp names + os.replace)."""
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f".{name}.{os.getpid()}.tmp"
        try:
            with open(self.fonts_dir / f"{name}.bdf", "rb") as fh:
                BdfFontFile.BdfFontFile(fh).save(f"{tmp}.pil")
            # .pbm first, so a .pil never appears without its glyph data.
            os.replace(f"{tmp}.pbm", directory / f"{name}.pbm")
            os.replace(f"{tmp}.pil", directory / f"{name}.pil")
        finally:
            for ext in (".pbm", ".pil"):
                try:
                    os.unlink(f"{tmp}{ext}")
                except FileNotFoundError:
                    pass
