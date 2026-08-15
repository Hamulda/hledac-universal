"""Entry point: python -m ruff_ext"""
from __future__ import annotations

import sys

from ruff_ext import main
from core import aclose

if __name__ == "__main__":
    sys.exit(main())
