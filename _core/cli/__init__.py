"""CLI layer for hledac universal — re-exports from args.py."""

from argparse import Namespace as _NS

from hledac.universal._core.cli.args import build_parser
from _core._util import aclose

__all__ = ["build_parser"]
