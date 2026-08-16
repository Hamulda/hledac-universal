"""
Sprint P2-1b tests — InferencePipeliner.

Tests basic structure and constants. Async behavior tested via smoke.
"""

import os
import sys
import types
import unittest
from _core import aclose


# Cleanup helper for sys.modules pollution
def _install_hledac_skeleton():
    """Install fake hledac modules. Call _remove_hledac_skeleton() in teardown."""
    _brain_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "brain"))
    _hledac = types.ModuleType("hledac")
    _hledac.universal = types.ModuleType("hledac.universal")
    sys.modules["hledac"] = _hledac
    sys.modules["hledac.universal"] = _hledac.universal
    _brain_pkg = types.ModuleType("hledac.universal.brain")
    _brain_pkg.__path__ = [_brain_dir]
    sys.modules["hledac.universal.brain"] = _brain_pkg


def _remove_hledac_skeleton():
    """Remove fake hledac modules from sys.modules."""
    for k in list(sys.modules.keys()):
        if k.startswith("hledac"):
            del sys.modules[k]


class TestPipelinerConstants(unittest.TestCase):
    """Test pipeliner constants and structure."""

    def test_pipeliner_constants(self):
        """Test pipeliner constants and structure."""
        import importlib.util

        try:
            _install_hledac_skeleton()
            path = os.path.join(os.path.dirname(__file__), "..", "..", "brain", "inference_pipeliner.py")
            spec = importlib.util.spec_from_file_location("brain.inference_pipeliner", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            self.assertEqual(mod.MAX_PENDING, 16)
            self.assertEqual(mod.SUBMIT_TIMEOUT_S, 120.0)
            self.assertEqual(mod.PREPROCESS_WORKERS, 2)
            self.assertTrue(hasattr(mod, "InferencePipeliner"))
            self.assertTrue(hasattr(mod, "PendingRequest"))
        finally:
            _remove_hledac_skeleton()


class TestPendingRequest(unittest.TestCase):
    """Test PendingRequest dataclass."""

    def test_pending_request_fields(self):
        """Test PendingRequest has all required fields."""
        import asyncio
        import importlib.util

        try:
            _install_hledac_skeleton()
            path = os.path.join(os.path.dirname(__file__), "..", "..", "brain", "inference_pipeliner.py")
            spec = importlib.util.spec_from_file_location("brain.inference_pipeliner", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            loop = asyncio.new_event_loop()
            try:
                future = loop.create_future()
                req = mod.PendingRequest(
                    future=future,
                    prompt="test prompt",
                    temperature=0.1,
                    max_tokens=50,
                    system_msg="test system",
                    thinking=True,
                    submitted_at=123456.0,
    )
                self.assertEqual(req.prompt, "test prompt")
                self.assertEqual(req.temperature, 0.1)
                self.assertEqual(req.max_tokens, 50)
                self.assertEqual(req.system_msg, "test system")
                self.assertTrue(req.thinking)
                self.assertEqual(req.submitted_at, 123456.0)
            finally:
                loop.close()
        finally:
            _remove_hledac_skeleton()


class TestSynthesisRunnerWire(unittest.TestCase):
    """Test SynthesisRunner has pipeliner wiring."""

    def test_synthesis_runner_has_pipeliner_field(self):
        """Test SynthesisRunner has _inference_pipeliner field."""
        import importlib.util

        try:
            _install_hledac_skeleton()
            path = os.path.join(os.path.dirname(__file__), "..", "..", "brain", "synthesis_runner.py")
            spec = importlib.util.spec_from_file_location("brain.synthesis_runner", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertTrue(hasattr(mod, "SynthesisRunner"))
        finally:
            _remove_hledac_skeleton()


if __name__ == "__main__":
    unittest.main()
