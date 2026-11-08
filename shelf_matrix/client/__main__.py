"""Entry point: python -m shelf_matrix.client --config /etc/shelf-matrix/config.toml"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from shelf_matrix.client.app import ClientApp
from shelf_matrix.config import ClientConfig, load_client_config


async def _main(config: ClientConfig) -> None:
    # SIGTERM/SIGINT cancel the app; ClientApp.run stops the supervisor (and
    # so clears the matrix) on the way out.
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    log = logging.getLogger(__name__)

    def on_signal() -> None:
        # Only the first signal cancels: a second one must not interrupt the
        # supervisor's stop and orphan the child.
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, log.info, "already shutting down")
        task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, on_signal)
    try:
        await ClientApp(config).run()
    except asyncio.CancelledError:
        log.info("shutting down")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m shelf_matrix.client")
    parser.add_argument("--config", type=Path, default=Path("/etc/shelf-matrix/config.toml"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_main(load_client_config(args.config)))


if __name__ == "__main__":
    main()
