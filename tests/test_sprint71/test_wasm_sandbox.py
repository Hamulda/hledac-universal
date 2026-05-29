"""
Test WASM Sandbox - Sprint 71
"""
import unittest


class TestWasmSandbox(unittest.TestCase):
    """Test WASM sandbox functionality."""

    def test_wasm_sandbox_creation(self):
        """Test WasmSandbox can be created."""
        from hledac.universal.tools.wasm_sandbox import WasmSandbox

        sandbox = WasmSandbox(
            fuel_limit=1000,
            epoch_deadline=10,
            timeout=30
        )

        # Check attributes
        self.assertEqual(sandbox.fuel_limit, 1000)
        self.assertEqual(sandbox.epoch_deadline, 10)
        self.assertEqual(sandbox.timeout, 30)

    def test_wasm_sandbox_defaults(self):
        """Test default values."""
        from hledac.universal.tools.wasm_sandbox import WasmSandbox

        sandbox = WasmSandbox()

        self.assertEqual(sandbox.fuel_limit, 1_000_000)
        self.assertEqual(sandbox.epoch_deadline, 30)
        self.assertEqual(sandbox.timeout, 60)

    def test_wasm_sandbox_stats(self):
        """Test stats retrieval."""
        from hledac.universal.tools.wasm_sandbox import WasmSandbox

        sandbox = WasmSandbox()
        stats = sandbox.get_stats()

        self.assertIn('available', stats)
        self.assertIn('fuel_limit', stats)
        self.assertIn('epoch_deadline', stats)


if __name__ == '__main__':
    unittest.main()
