"""CLI layer for hledac universal — argparse schema + command dispatch."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import cyclopts
from cyclopts import App as CycloptsApp

from hledac.universal.core.cli.args import build_parser

if TYPE_CHECKING:
    pass


# Shared app instance — commands are registered via @app.command()
app = CycloptsApp(
    name="hledac",
    help="Hledac Universal OSINT Orchestrator",
    version="8RA",
)


def _resolve_rl_args(args: argparse.Namespace) -> argparse.Namespace:
    """F261QMIX: resolve --rl-no-train / --rl-train-interval overrides."""
    if getattr(args, "rl_no_train", False):
        args.rl_train = False
    if getattr(args, "rl_train_interval", None) is not None:
        os.environ["HLEDAC_RL_TRAIN_INTERVAL"] = str(args.rl_train_interval)
    return args


def _configure_logging() -> None:
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Suppress coremltools warnings about missing native libs on py3.14.
    class _CoremlNativeLibFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if record.name == "coremltools" and record.levelno == logging.WARNING:
                msg = record.getMessage()
                if (
                    "Failed to load _ML" in msg
                    or "Failed to load '" in msg
                    or "Fail to import Blob" in msg
                ):
                    return False
            return True

    _coreml_logger = logging.getLogger("coremltools")
    _coreml_logger.propagate = False
    _coreml_handler = logging.NullHandler()
    _coreml_handler.addFilter(_CoremlNativeLibFilter())
    _coreml_logger.addHandler(_coreml_handler)


def run() -> None:
    """Main entry point — thin facade, delegates to cyclopts."""
    app()
