"""BDF font loading with a converted-.pil on-disk cache."""
from __future__ import annotations

from pathlib import Path

from PIL import BdfFontFile, ImageFont


class FontCache:
    def __init__(self, fonts_dir: Path, cache_dir: Path) -> None:
        self.fonts_dir = Path(fonts_dir)
        self.cache_dir = Path(cache_dir)
        self._loaded: dict[str, ImageFont.ImageFont] = {}

    def names(self) -> list[str]:
        return sorted(p.stem for p in self.fonts_dir.glob("*.bdf"))

    def get(self, name: str) -> ImageFont.ImageFont:
        if name in self._loaded:
            return self._loaded[name]
        bdf = self.fonts_dir / f"{name}.bdf"
        if name not in self.names():
            raise KeyError(name)
        pil = self.cache_dir / f"{name}.pil"
        if not pil.exists():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with open(bdf, "rb") as fh:
                BdfFontFile.BdfFontFile(fh).save(str(self.cache_dir / name))
        font = ImageFont.load(str(pil))
        self._loaded[name] = font
        return font
