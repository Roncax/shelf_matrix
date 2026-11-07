import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
CLOCK_DIR = REPO / "programs" / "clock"
sys.path.insert(0, str(CLOCK_DIR))

import faces  # noqa: E402
from faces import render_analog, render_digital  # noqa: E402
from icons import draw_icon, weather_group  # noqa: E402

from shelf_matrix.program.base import ProgramContext  # noqa: E402
from shelf_matrix.program.fonts import FontCache  # noqa: E402
from shelf_matrix.schema import load_manifest  # noqa: E402

COLOR = (255, 154, 60)
ACCENT = (102, 204, 255)
DEFAULTS = {
    "style": "digital", "color": "#ff9a3c", "accent": "#66ccff", "font": "9x18B",
    "hour24": True, "seconds": False, "location": "",
}
WEATHER = {
    "location": "Rome", "temp_c": 12.4, "code": 63, "is_day": True,
    "min_c": 8.0, "max_c": 15.0, "fetched_at": "2026-11-07T21:00:00",
}


@pytest.fixture
def fonts(tmp_path):
    return FontCache(REPO / "fonts", tmp_path)


def blank():
    return Image.new("RGB", (64, 64))


def colors(im):
    return {c for _, c in im.getcolors(64 * 64)}


def load_program_module():
    spec = importlib.util.spec_from_file_location("clock_program_under_test", CLOCK_DIR / "program.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_weather_groups():
    assert weather_group(0, True) == "clear_day" and weather_group(0, False) == "clear_night"
    assert weather_group(63, True) == "rain" and weather_group(95, True) == "storm"
    assert weather_group(1234, True) == "cloudy"
    assert weather_group(2, False) == "partly" and weather_group(3, True) == "cloudy"
    assert weather_group(48, True) == "fog" and weather_group(53, True) == "drizzle"
    assert weather_group(81, True) == "rain" and weather_group(86, True) == "snow"


@pytest.mark.parametrize("group", ["clear_day", "clear_night", "partly", "cloudy", "fog",
                                   "drizzle", "rain", "snow", "storm"])
def test_every_icon_draws_inside_its_box(group):
    img = blank()
    draw_icon(img, 20, 20, group)
    box = img.getbbox()
    assert box is not None
    assert box[0] >= 20 and box[1] >= 20 and box[2] <= 29 and box[3] <= 29


def test_digital_face_draws_time_in_color(fonts):
    img = blank()
    render_digital(img, datetime(2026, 11, 7, 21, 47), DEFAULTS, WEATHER, fonts)
    assert COLOR in colors(img.crop((0, 0, 64, 40)))


def test_digital_face_draws_accent_weather(fonts):
    img = blank()
    render_digital(img, datetime(2026, 11, 7, 21, 47), DEFAULTS, WEATHER, fonts)
    assert ACCENT in colors(img.crop((0, 44, 64, 64)))


def test_digital_face_renders_without_weather(fonts):
    img = blank()
    render_digital(img, datetime(2026, 11, 7, 9, 5), DEFAULTS, None, fonts)
    assert img.getbbox() is not None


def test_analog_hands_point_correctly(fonts):
    cx, cy = faces.DIAL_CENTER
    three, twelve = blank(), blank()
    render_analog(three, datetime(2026, 1, 1, 3, 0, 0), DEFAULTS, None, fonts)
    render_analog(twelve, datetime(2026, 1, 1, 12, 0, 0), DEFAULTS, None, fonts)
    # hour hand at 3 points right (minute hand at 12 points up)
    assert three.getpixel((cx + 6, cy)) == COLOR
    assert three.getpixel((cx, cy - 10)) == COLOR
    assert twelve.getpixel((cx, cy - 10)) == COLOR
    assert twelve.getpixel((cx + 6, cy)) != COLOR


def test_analog_second_hand_in_accent(fonts):
    cx, cy = faces.DIAL_CENTER
    img = blank()
    render_analog(img, datetime(2026, 1, 1, 12, 0, 15), {**DEFAULTS, "seconds": True}, None, fonts)
    assert img.getpixel((cx + 12, cy)) == ACCENT


def test_analog_renders_with_weather(fonts):
    img = blank()
    render_analog(img, datetime(2026, 1, 1, 10, 10, 0), DEFAULTS, WEATHER, fonts)
    assert ACCENT in colors(img)


def test_twelve_hour_format_changes_output(fonts):
    a, b = blank(), blank()
    render_digital(a, datetime(2026, 1, 1, 21, 0), DEFAULTS, None, fonts)
    render_digital(b, datetime(2026, 1, 1, 21, 0), {**DEFAULTS, "hour24": False}, None, fonts)
    assert a.tobytes() != b.tobytes()


def manifest_fonts():
    m = load_manifest(CLOCK_DIR)
    return next(p for p in m.params if p.name == "font").options


@pytest.mark.parametrize("font", manifest_fonts())
@pytest.mark.parametrize("hour24", [True, False])
def test_every_font_option_fits(fonts, font, hour24):
    img = blank()
    params = {**DEFAULTS, "font": font, "hour24": hour24}
    render_digital(img, datetime(2026, 1, 1, 23, 59), params, WEATHER, fonts)
    # the time block must not be clipped: first/last columns of the time area stay empty
    top = img.crop((0, 0, 64, 40))
    box = top.getbbox()
    assert box is not None and box[0] >= 1 and box[2] <= 63


def test_manifest_loads():
    m = load_manifest(CLOCK_DIR)
    assert m.id == "clock" and m.entry == "program:ClockProgram" and m.needs == ("weather",)
    assert {p.name for p in m.params} == set(DEFAULTS)


def test_program_fps_and_draw(fonts):
    mod = load_program_module()
    ctx = ProgramContext(params=dict(DEFAULTS), globals={}, data={"weather": {"Rome": WEATHER}}, fonts=fonts)
    prog = mod.ClockProgram(ctx)
    assert prog.fps == 1
    img = blank()
    ctx.params["location"] = "Rome"
    prog.draw(img, datetime(2026, 11, 7, 21, 47))
    assert ACCENT in colors(img)
    assert prog.align_to_second is True
    ctx.params.update(style="analog", seconds=True)
    assert prog.fps == 1 and prog.align_to_second is True
    ctx.params["seconds"] = False
    assert prog.fps == 1
    img2 = blank()
    prog.draw(img2, datetime(2026, 11, 7, 21, 47))
    assert img2.tobytes() != img.tobytes()


ODD_WEATHER = [{"temp_c": None}, {}, {"temp_c": 12.3}, {"temp_c": float("nan")},
               {"temp_c": 5, "code": None, "is_day": None}, "garbage"]


@pytest.mark.parametrize("weather", ODD_WEATHER)
@pytest.mark.parametrize("render", [render_digital, render_analog])
def test_odd_weather_payloads_do_not_crash(fonts, render, weather):
    img = blank()
    render(img, datetime(2026, 1, 1, 10, 10), DEFAULTS, weather, fonts)
    assert img.getbbox() is not None


@pytest.mark.parametrize("topic", [None, "x", [], {"": None}, {"": {"temp_c": None}}])
@pytest.mark.parametrize("style", ["digital", "analog"])
def test_program_tolerates_odd_weather_topic(fonts, topic, style):
    mod = load_program_module()
    ctx = ProgramContext(params={**DEFAULTS, "style": style}, globals={}, data={"weather": topic}, fonts=fonts)
    mod.ClockProgram(ctx).draw(blank(), datetime(2026, 1, 1, 10, 10))
