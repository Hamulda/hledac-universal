"""CLI layer for hledac universal — re-exports from args.py."""

from argparse import Namespace as _NS

from hledac.universal.core.cli.args import build_parser
from core._util import aclose

__all__ = ["build_parser"]
