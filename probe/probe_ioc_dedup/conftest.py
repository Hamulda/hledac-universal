"""Pytest configuration for probe_ioc_dedup tests."""


import sys
from pathlib import Path
from core import aclose

# Add project root to sys.path for imports like `from tools.ioc_dedup`
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
