import types
from pathlib import Path

import pytest

from shelf_matrix.config import MatrixOptions, load_client_config


def test_default_flags_match_owner_command():
    assert MatrixOptions().to_cli_flags(70) == [
        "--led-rows=64", "--led-cols=64", "--led-gpio-mapping=adafruit-hat-pwm",
        "--led-pwm-bits=9", "--led-pwm-lsb-nanoseconds=50", "--led-limit-refresh=120",
        "--led-pixel-mapper=Rotate:90", "--led-no-hardware-pulse", "--led-brightness=70"]


def test_flags_with_slowdown_and_pulsing_enabled():
    flags = MatrixOptions(disable_hardware_pulsing=False, gpio_slowdown=2).to_cli_flags(10)
    assert "--led-no-hardware-pulse" not in flags
    assert flags[-2:] == ["--led-slowdown-gpio=2", "--led-brightness=10"]


def test_example_config_loads():
    p = Path("deploy/config.example.toml")
    cfg = load_client_config(p)
    assert cfg.matrix == MatrixOptions() and cfg.fallback_program == "clock"
    assert cfg.path == p and cfg.library_dir == Path("/opt/rpi-rgb-led-matrix")
    assert cfg.token == "change-me"


def test_missing_server_url_is_error(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('token = "x"\n')
    with pytest.raises(ValueError, match="server_url"):
        load_client_config(f)


def test_apply_to_sets_attributes():
    o = types.SimpleNamespace()
    MatrixOptions(gpio_slowdown=2).apply_to(o)
    assert o.pixel_mapper_config == "Rotate:90" and o.gpio_slowdown == 2
    assert o.disable_hardware_pulsing is True


def test_apply_to_skips_none_slowdown():
    o = types.SimpleNamespace()
    MatrixOptions().apply_to(o)
    assert not hasattr(o, "gpio_slowdown")
