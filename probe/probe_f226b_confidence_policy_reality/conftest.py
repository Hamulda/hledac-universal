"""Shared fixtures for F226B tests."""

import sys
from pathlib import Path

# Ensure bare imports work by setting up the path correctly
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))
# Also add parent of universal so 'intelligence.confidence_policy' works
_parent = _root.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))
