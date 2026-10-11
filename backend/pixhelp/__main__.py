"""Entry point: python -m pixhelp [--config path/to/config.yaml]."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import ConfigError, load_config
from .logging_setup import setup_logging
from .main import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="pixhelp", description=__doc__)
    parser.add_argument("--config", default=None, help="path to config.yaml")
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        sys.exit(f"config error: {exc}")

    setup_logging(cfg.logging)

    if sys.platform != "win32":
        sys.exit("the pixhelp backend requires native Windows (dxcam + WinRT)")

    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("interrupted; shutting down")


if __name__ == "__main__":
    main()
