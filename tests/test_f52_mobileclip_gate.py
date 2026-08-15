"""F5.2: MobileCLIP opt-in env gate."""

import pytest
from _core import aclose


class TestF52MobileCLIPGate:
    """Verify HLEDAC_ENABLE_MOBILECLIP env gate is respected."""

    @pytest.mark.asyncio
    async def test_mobileclip_gated_off_by_default(self, monkeypatch: pytest.MonkeyPatch):
        """Without env var, _lazy_load() raises RuntimeError (not ImportError)."""
        monkeypatch.delenv("HLEDAC_ENABLE_MOBILECLIP", raising=False)
        from multimodal.fusion import MobileCLIPFusion

        f = MobileCLIPFusion()
        with pytest.raises(RuntimeError) as exc:
            await f._lazy_load()
        assert "HLEDAC_ENABLE_MOBILECLIP" in str(exc.value)

    @pytest.mark.asyncio
    async def test_mobileclip_gated_off_with_zero(self, monkeypatch: pytest.MonkeyPatch):
        """HLEDAC_ENABLE_MOBILECLIP=0 still keeps the gate closed."""
        monkeypatch.setenv("HLEDAC_ENABLE_MOBILECLIP", "0")
        from multimodal.fusion import MobileCLIPFusion

        f = MobileCLIPFusion()
        with pytest.raises(RuntimeError):
            await f._lazy_load()

    @pytest.mark.asyncio
    async def test_mobileclip_gated_on_proceeds_to_import(self, monkeypatch: pytest.MonkeyPatch):
        """HLEDAC_ENABLE_MOBILECLIP=1 reaches the import step (fails on missing pkg)."""
        monkeypatch.setenv("HLEDAC_ENABLE_MOBILECLIP", "1")
        from multimodal.fusion import MobileCLIPFusion

        f = MobileCLIPFusion()
        # mobileclip package is not installed in test env, so we expect
        # ImportError (gate opened, dep check failed).
        with pytest.raises(ImportError):
            await f._lazy_load()

    @pytest.mark.asyncio
    async def test_mobileclip_gate_accepts_truthy_strings(self, monkeypatch: pytest.MonkeyPatch):
        """Gate accepts 1, true, yes (case-insensitive)."""
        for value in ("1", "true", "TRUE", "yes"):
            monkeypatch.setenv("HLEDAC_ENABLE_MOBILECLIP", value)
            from multimodal.fusion import MobileCLIPFusion

            f = MobileCLIPFusion()
            with pytest.raises(ImportError):
                await f._lazy_load()
