"""CLI layer for hledac universal — re-exports from args.py."""

from argparse import Namespace as _NS

from hledac.universal.core.cli.args import build_parser

__all__ = ["build_parser"]
