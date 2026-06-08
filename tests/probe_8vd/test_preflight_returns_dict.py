"""Test preflight check returns valid dict."""
import asyncio
import os
import pathlib
import sys

_universal = pathlib.Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(_universal))

# Import _preflight_check from the actual __main__ module
import importlib
import hledac.universal.__main__ as main_mod


def test_preflight_dict():
    """_preflight_check returns dict with required keys."""
    _prev_cwd = os.getcwd()
    try:
        os.chdir(_universal)
        result = asyncio.run(main_mod._preflight_check())
        assert isinstance(result, dict)
        assert "metal" in result
        assert "free_ram_mb" in result
        assert "memory_pct" in result
    finally:
        os.chdir(_prev_cwd)


if __name__ == "__main__":
    test_preflight_dict()
    print("test_preflight_returns_dict: PASSED")
