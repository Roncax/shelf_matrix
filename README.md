# shelf-matrix

Python package `shelf_matrix` for driving an LED matrix on a Raspberry Pi:
a server (Docker) that runs programs, a Pi client that displays frames, and
the programs themselves. Each program ships a `manifest.toml` describing its
tunable parameters.

## Development

    python3 -m venv .venv
    .venv/bin/pip install -e '.[dev]'
    .venv/bin/pytest

## Server deployment

The server image is built by GitHub Actions and published to
`ghcr.io/roncax/shelf-matrix-server` (amd64 and arm64).

On the home server, put `deploy/docker-compose.yml`, `deploy/.env.example`
(copied to `.env` with `SHELF_TOKEN` set, e.g. `openssl rand -hex 32`) and
`deploy/server-update.sh` in `/opt/shelf-matrix-server`, then:

    cd /opt/shelf-matrix-server
    docker compose up -d

The image is pulled from GHCR. Either make the package public (simplest, the
image contains no secrets) in the GitHub package settings, or authenticate once
with a personal access token that has the `read:packages` scope:

    docker login ghcr.io -u Roncax

Auto-update: install the systemd units to pull a new image every 15 minutes.

    sudo cp deploy/shelf-matrix-update.{service,timer} /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now shelf-matrix-update.timer

The service runs `/opt/shelf-matrix-server/server-update.sh
/opt/shelf-matrix-server`; edit the unit if you use another directory.

## License

GPL-2.0-or-later. See LICENSE.
