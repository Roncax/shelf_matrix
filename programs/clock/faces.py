"""Digital and analog clock faces for a 64x64 panel."""
from __future__ import annotations

import math
from datetime import datetime

from PIL import Image, ImageDraw

from icons import draw_icon, weather_group

DIAL_CENTER = (32, 27)
DIAL_RADIUS = 24
SMALL_FONT = "5x7"
STRIP_Y = 55
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DEFAULT_COLOR = "#ff9a3c"
DEFAULT_ACCENT = "#66ccff"


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _dim(rgb: tuple[int, int, int], f: float) -> tuple[int, int, int]:
    return tuple(int(c * f) for c in rgb)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return int(draw.textlength(text, font=font))


def _temp_text(weather: dict | None) -> str:
    if weather is None:
        return "--\N{DEGREE SIGN}"
    return f"{round(weather['temp_c'])}\N{DEGREE SIGN}"


def _date_text(now: datetime) -> str:
    return f"{DAYS[now.weekday()]} {now.day:02d}"


def _draw_weather(img, draw, weather, right: int, y: int, accent, fonts) -> None:
    """Icon + temperature, right-aligned at x=`right`."""
    font = fonts.get(SMALL_FONT)
    text = _temp_text(weather)
    tx = right - _text_width(draw, text, font)
    draw.text((tx, y), text, font=font, fill=accent)
    if weather is not None:
        group = weather_group(weather["code"], weather["is_day"])
        draw_icon(img, tx - 10, y - 1, group)


def _time_text(now: datetime, params: dict) -> tuple[str, str]:
    if params.get("hour24", True):
        return f"{now.hour:02d}:{now.minute:02d}", ""
    hour = now.hour % 12 or 12
    return f"{hour}:{now.minute:02d}", "PM" if now.hour >= 12 else "AM"


def render_digital(img, now: datetime, params: dict, weather: dict | None, fonts) -> None:
    draw = ImageDraw.Draw(img)
    color = _rgb(params.get("color", DEFAULT_COLOR))
    accent = _rgb(params.get("accent", DEFAULT_ACCENT))
    font = fonts.get(params.get("font", "9x18B"))
    small = fonts.get(SMALL_FONT)

    text, suffix = _time_text(now, params)
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    w, h = r - l, b - t
    area_h = 50
    x = (64 - w) // 2 - l
    y = (area_h - h) // 2 - t
    draw.text((x, y), text, font=font, fill=color)
    if suffix:
        draw.text((64 - _text_width(draw, suffix, small) - 2, y + t + h + 2), suffix, font=small, fill=accent)
    if params.get("seconds"):
        n = now.second
        draw.line((2, 52, 2 + n, 52), fill=_dim(color, 0.45))
        draw.point((2 + n, 52), fill=color)

    draw.text((1, STRIP_Y), _date_text(now), font=small, fill=color)
    _draw_weather(img, draw, weather, 63, STRIP_Y, accent, fonts)


def _hand_end(angle_deg: float, length: float) -> tuple[int, int]:
    cx, cy = DIAL_CENTER
    a = math.radians(angle_deg)
    return round(cx + length * math.sin(a)), round(cy - length * math.cos(a))


def render_analog(img, now: datetime, params: dict, weather: dict | None, fonts) -> None:
    draw = ImageDraw.Draw(img)
    color = _rgb(params.get("color", DEFAULT_COLOR))
    accent = _rgb(params.get("accent", DEFAULT_ACCENT))
    small = fonts.get(SMALL_FONT)
    cx, cy = DIAL_CENTER

    for i in range(12):
        quarter = i % 3 == 0
        inner = DIAL_RADIUS - (4 if quarter else 2)
        draw.line(
            (_hand_end(i * 30, inner), _hand_end(i * 30, DIAL_RADIUS)),
            fill=color if quarter else _dim(color, 0.6),
        )

    minute = now.minute + now.second / 60
    hour = (now.hour % 12) + minute / 60
    draw.line((cx, cy, *_hand_end(hour * 30, 12)), fill=color, width=2)
    draw.line((cx, cy, *_hand_end(minute * 6, 19)), fill=color)
    if params.get("seconds"):
        draw.line((cx, cy, *_hand_end(now.second * 6, 21)), fill=accent)
    draw.point((cx, cy), fill=accent)

    draw.text((1, STRIP_Y), _date_text(now), font=small, fill=color)
    _draw_weather(img, draw, weather, 63, STRIP_Y, accent, fonts)
