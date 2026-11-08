"""Program base class and context."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from PIL import Image

from shelf_matrix.program.fonts import FontCache


@dataclass
class ProgramContext:
    params: dict
    globals: dict
    data: dict[str, dict[str, Any]]
    fonts: FontCache
    width: int = 64
    height: int = 64


class Program:
    fps: float = 1.0
    # With fps == 1, tick just after each wall-clock second instead of every
    # 1 s of elapsed time (which drifts and occasionally skips a second).
    align_to_second: bool = False

    def __init__(self, ctx: ProgramContext) -> None:
        self.ctx = ctx

    def on_update(self) -> None:
        """Called after params, globals or data change."""

    def draw(self, img: Image.Image, now: datetime) -> None:
        raise NotImplementedError
