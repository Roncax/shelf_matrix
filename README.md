# shelf-matrix

Python package `shelf_matrix` for driving an LED matrix on a Raspberry Pi:
a server (Docker) that runs programs, a Pi client that displays frames, and
the programs themselves. Each program ships a `manifest.toml` describing its
tunable parameters.

## Development

    python3 -m venv .venv
    .venv/bin/pip install -e '.[dev]'
    .venv/bin/pytest

Deployment instructions will be added later.

## License

GPL-2.0-or-later. See LICENSE.
