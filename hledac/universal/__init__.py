"""hledac.universal package — re-export from universal/ root."""
from __future__ import annotations

import sys
from pathlib import Path

_universal_root = Path(__file__).parent.parent.parent
if str(_universal_root) not in sys.path:
    sys.path.insert(0, str(_universal_root))

from universal import *
__all__ = []
