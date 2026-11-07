"""Clock + weather program."""
from __future__ import annotations

from datetime import datetime

from faces import render_analog, render_digital
from shelf_matrix.program.base import Program


class ClockProgram(Program):
    fps = 1.0
    align_to_second = True  # seconds never skip, in either style

    def draw(self, img, now: datetime) -> None:
        p = self.ctx.params
        topic = self.ctx.data.get("weather")
        weather = topic.get(p.get("location", "")) if isinstance(topic, dict) else None
        render = render_analog if p.get("style") == "analog" else render_digital
        render(img, now, p, weather, self.ctx.fonts)
