"""F5.2: MobileCLIP opt-in env gate."""

import os

import pytest


class TestF52MobileCLIPGate:
    """Verify HLEDAC_ENABLE_MOBILECLIP env gate is respected."""

    @pytest.fixture(autouse=True)
    def _clean_env(self):
        """Ensure HLEDAC_ENABLE_MOBILECLIP is unset for each test."""
        saved = os.environ.pop("HLEDAC_ENABLE_MOBILECLIP", None)
        yield
        if saved is not None:
            os.environ["HLEDAC_ENABLE_MOBILECLIP"] = saved
        else:
            os.environ.pop("HLEDAC_ENABLE_MOBILECLIP", None)

    @pytest.mark.asyncio
    async def test_mobileclip_gated_off_by_default(self):
        """Without env var, _lazy_load() raises RuntimeError (not ImportError)."""
        from multimodal.fusion import MobileCLIPFusion
        f = MobileCLIPFusion()
        with pytest.raises(RuntimeError) as exc:
            await f._lazy_load()
        assert "HLEDAC_ENABLE_MOBILECLIP" in str(exc.value)

    @pytest.mark.asyncio
    async def test_mobileclip_gated_off_with_zero(self):
        """HLEDAC_ENABLE_MOBILECLIP=0 still keeps the gate closed."""
        os.environ["HLEDAC_ENABLE_MOBILECLIP"] = "0"
        from multimodal.fusion import MobileCLIPFusion
        f = MobileCLIPFusion()
        with pytest.raises(RuntimeError):
            await f._lazy_load()

    @pytest.mark.asyncio
    async def test_mobileclip_gated_on_proceeds_to_import(self):
        """HLEDAC_ENABLE_MOBILECLIP=1 reaches the import step (fails on missing pkg)."""
        os.environ["HLEDAC_ENABLE_MOBILECLIP"] = "1"
        from multimodal.fusion import MobileCLIPFusion
        f = MobileCLIPFusion()
        # mobileclip package is not installed in test env, so we expect
        # ImportError (gate opened, dep check failed).
        with pytest.raises(ImportError):
            await f._lazy_load()

    @pytest.mark.asyncio
    async def test_mobileclip_gate_accepts_truthy_strings(self):
        """Gate accepts 1, true, yes (case-insensitive)."""
        for value in ("1", "true", "TRUE", "yes"):
            os.environ["HLEDAC_ENABLE_MOBILECLIP"] = value
            from multimodal.fusion import MobileCLIPFusion
            f = MobileCLIPFusion()
            with pytest.raises(ImportError):
                await f._lazy_load()
