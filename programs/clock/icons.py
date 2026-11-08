"""Weather group mapping and 9x9 pixel-art weather icons."""
from __future__ import annotations

from PIL import Image

_RANGES = (
    (range(0, 1), None),  # clear: depends on is_day
    (range(1, 3), "partly"),
    (range(3, 4), "cloudy"),
    ((45, 48), "fog"),
    (range(51, 58), "drizzle"),
    (list(range(61, 68)) + list(range(80, 83)), "rain"),
    (list(range(71, 78)) + [85, 86], "snow"),
    (range(95, 100), "storm"),
)


def weather_group(code: int, is_day: bool) -> str:
    """Map a WMO weather code to an icon group."""
    for codes, group in _RANGES:
        if code in codes:
            if group is None:
                return "clear_day" if is_day else "clear_night"
            return group
    return "cloudy"


PALETTE = {
    "Y": (255, 200, 40),    # sun
    "M": (200, 205, 235),   # moon
    "G": (170, 180, 195),   # cloud
    "D": (110, 118, 135),   # storm cloud
    "B": (70, 140, 255),    # rain
    "b": (130, 175, 235),   # drizzle
    "W": (245, 245, 255),   # snow
    "L": (255, 225, 60),    # lightning
}

_CLOUD = (
    "...GGG...",
    "..GGGGGG.",
    ".GGGGGGGG",
    "GGGGGGGGG",
    ".GGGGGGG.",
)

ART: dict[str, tuple[str, ...]] = {
    "clear_day": (
        "....Y....",
        ".Y.....Y.",
        "...YYY...",
        "..YYYYY..",
        "Y.YYYYY.Y",
        "..YYYYY..",
        "...YYY...",
        ".Y.....Y.",
        "....Y....",
    ),
    "clear_night": (
        "...MMMM..",
        "..MMM....",
        ".MMM.....",
        ".MMM.....",
        ".MMM.....",
        ".MMM....M",
        "..MMM..MM",
        "...MMMMM.",
        ".....MM..",
    ),
    "partly": (
        "..Y......",
        "Y..Y..Y..",
        "..YYY....",
        ".YYYGGG..",
        "..YGGGGGG",
        "..GGGGGGG",
        ".GGGGGGGG",
        "..GGGGGG.",
        ".........",
    ),
    "cloudy": (
        ".........",
        "...GGG...",
        "..GGGGGG.",
        ".GGGGGGGG",
        "GGGGGGGGG",
        "GGGGGGGGG",
        ".GGGGGGG.",
        ".........",
        ".........",
    ),
    "fog": (
        ".........",
        "GGGGGGG..",
        ".........",
        ".GGGGGGG.",
        ".........",
        "GGGGGGG..",
        ".........",
        ".GGGGGGG.",
        ".........",
    ),
    "drizzle": _CLOUD + (".........", ".b..b..b.", ".........", "..b..b..."),
    "rain": _CLOUD + (".........", ".B..B..B.", "B..B..B..", "........."),
    "snow": _CLOUD + (".........", ".W..W..W.", "..W..W...", ".W..W..W."),
    "storm": tuple(r.replace("G", "D") for r in _CLOUD) + (
        "....LL...", "...LL....", "..LLLL...", "....L....",
    ),
}


def draw_icon(img: Image.Image, x: int, y: int, group: str, size: int = 9) -> None:
    """Draw a weather icon with its top-left corner at (x, y)."""
    rows = ART[group]
    tile = Image.new("RGBA", (9, 9))
    for ry, row in enumerate(rows):
        for rx, ch in enumerate(row):
            if ch in PALETTE:
                tile.putpixel((rx, ry), PALETTE[ch] + (255,))
    if size != 9:
        tile = tile.resize((size, size), Image.NEAREST)
    img.paste(tile, (x, y), tile)
