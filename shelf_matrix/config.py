"""Client configuration and LED matrix options."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib


@dataclass
class MatrixOptions:
    rows: int = 64
    cols: int = 64
    hardware_mapping: str = "adafruit-hat-pwm"
    pwm_bits: int = 9
    pwm_lsb_nanoseconds: int = 50
    limit_refresh_rate_hz: int = 120
    pixel_mapper_config: str = "Rotate:90"
    disable_hardware_pulsing: bool = True
    gpio_slowdown: int | None = None

    def to_cli_flags(self, brightness: int) -> list[str]:
        flags = [
            f"--led-rows={self.rows}",
            f"--led-cols={self.cols}",
            f"--led-gpio-mapping={self.hardware_mapping}",
            f"--led-pwm-bits={self.pwm_bits}",
            f"--led-pwm-lsb-nanoseconds={self.pwm_lsb_nanoseconds}",
            f"--led-limit-refresh={self.limit_refresh_rate_hz}",
            f"--led-pixel-mapper={self.pixel_mapper_config}",
        ]
        if self.disable_hardware_pulsing:
            flags.append("--led-no-hardware-pulse")
        if self.gpio_slowdown is not None:
            flags.append(f"--led-slowdown-gpio={self.gpio_slowdown}")
        flags.append(f"--led-brightness={brightness}")
        return flags

    def apply_to(self, opts: Any) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "gpio_slowdown" and value is None:
                continue
            setattr(opts, f.name, value)


@dataclass
class ClientConfig:
    server_url: str
    token: str
    library_dir: Path
    programs_dir: Path
    fonts_dir: Path
    state_path: Path
    fallback_program: str = "clock"
    matrix: MatrixOptions = field(default_factory=MatrixOptions)
    path: Path | None = None


_REQUIRED = ("server_url", "token", "library_dir", "programs_dir", "fonts_dir", "state_path")
_PATH_KEYS = ("library_dir", "programs_dir", "fonts_dir", "state_path")


def load_client_config(path: Path) -> ClientConfig:
    path = Path(path)
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    for key in _REQUIRED:
        if key not in data:
            raise ValueError(f"missing required config key: {key}")
    kwargs: dict[str, Any] = {k: data[k] for k in _REQUIRED}
    for key in _PATH_KEYS:
        kwargs[key] = Path(kwargs[key])
    if "fallback_program" in data:
        kwargs["fallback_program"] = data["fallback_program"]
    matrix = data.get("matrix", {})
    known = {f.name for f in fields(MatrixOptions)}
    unknown = set(matrix) - known
    if unknown:
        raise ValueError(f"unknown matrix option: {', '.join(sorted(unknown))}")
    return ClientConfig(matrix=MatrixOptions(**matrix), path=path, **kwargs)
