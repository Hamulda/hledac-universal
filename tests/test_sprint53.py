"""
Sprint 53 tests – MPS ELA, MPS stego, AMX sketch.
"""

import io
import sys
import time
import unittest
from unittest.mock import patch

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

# Check availability once at module level
try:
    import torch
    _MPS_AVAILABLE = torch.backends.mps.is_available()
except Exception:
    _MPS_AVAILABLE = False

try:
    _MLX_AVAILABLE = True
except Exception:
    _MLX_AVAILABLE = False


# =============================================================================
# MPS ELA
# =============================================================================

@unittest.skipIf(not _PIL_AVAILABLE, "PIL not available")
class TestMPSELA(unittest.IsolatedAsyncioTestCase):
    """Testy pro MPS akcelerovanou ELA."""

    async def asyncSetUp(self):
        from hledac.universal.intelligence.document_intelligence import DeepForensicsAnalyzer
        self.analyzer = DeepForensicsAnalyzer()
        # Vytvoříme testovací obrázek 1024×1024
        img = Image.new('RGB', (1024, 1024), color='gray')
        self.img_bytes = io.BytesIO()
        img.save(self.img_bytes, format='JPEG')
        self.img_bytes = self.img_bytes.getvalue()

    @unittest.skipIf(not _MPS_AVAILABLE, "MPS not available")
    async def test_mps_ela_accuracy(self):
        """Porovnání MPS a CPU výsledků – tolerance 0.05."""
        cpu_score = await self.analyzer._ela_analysis_cpu(self.img_bytes)
        mps_score = await self.analyzer._ela_analysis_mps(self.img_bytes)
        self.assertAlmostEqual(cpu_score, mps_score, delta=0.05)

    @unittest.skipIf(not _MPS_AVAILABLE, "MPS not available")
    async def test_mps_ela_speed(self):
        """Měření rychlosti – ≤ 200 ms pro 1024×1024."""
        start = time.time()
        await self.analyzer._ela_analysis_mps(self.img_bytes)
        elapsed = (time.time() - start) * 1000
        self.assertLess(elapsed, 200)

    async def test_mps_ela_size_limit(self):
        """Obrázky > 2048×2048 se zmenší (volá se resize)."""
        # Vytvoříme velký obrázek
        large_img = Image.new('RGB', (3000, 3000), color='gray')
        large_bytes = io.BytesIO()
        large_img.save(large_bytes, format='JPEG')
        large_bytes = large_bytes.getvalue()

        # Mockneme resize a ověříme, že byl zavolán
        with patch('PIL.Image.Image.resize') as mock_resize:
            mock_resize.return_value = Image.new('RGB', (2048, 2048))
            if _MPS_AVAILABLE:
                await self.analyzer._ela_analysis_mps(large_bytes)
            else:
                await self.analyzer._ela_analysis_cpu(large_bytes)
            mock_resize.assert_called_once()

    async def test_mps_fallback(self):
        """Pokud MPS není k dispozici, použije se CPU."""
        with patch('hledac.universal.intelligence.document_intelligence.MPS_AVAILABLE', False):
            from hledac.universal.intelligence import document_intelligence
            analyzer = document_intelligence.DeepForensicsAnalyzer()
            with patch.object(analyzer, '_ela_analysis_cpu', return_value=0.5) as mock_cpu:
                result = await analyzer._ela_analysis(self.img_bytes)
                mock_cpu.assert_called_once()
                self.assertEqual(result, 0.5)


# =============================================================================
# MPS Stego
# =============================================================================

@unittest.skipIf(not _PIL_AVAILABLE, "PIL not available")
class TestMPSStego(unittest.IsolatedAsyncioTestCase):
    """Testy pro MPS steganografii."""

    async def asyncSetUp(self):
        from hledac.universal.security.stego_detector import StatisticalStegoDetector
        self.detector = StatisticalStegoDetector()
        # Vytvoříme testovací obrázek 1024×1024 (odstíny šedi)
        img = Image.new('L', (1024, 1024), color=128)
        self.img_bytes = io.BytesIO()
        img.save(self.img_bytes, format='PNG')
        self.img_bytes = self.img_bytes.getvalue()

    @unittest.skipIf(not _MPS_AVAILABLE, "MPS not available")
    async def test_mps_stego_accuracy(self):
        """Obě metody vrací validní skóre."""
        cpu_result = await self.detector._detect_cpu(self.img_bytes)
        mps_result = await self.detector._detect_mps(self.img_bytes)
        # Both should return valid scores
        self.assertIn('score', cpu_result)
        self.assertIn('score', mps_result)
        self.assertIsInstance(cpu_result['score'], float)
        self.assertIsInstance(mps_result['score'], float)
        # Scores should be in valid range
        self.assertGreaterEqual(cpu_result['score'], 0.0)
        self.assertLessEqual(cpu_result['score'], 1.0)

    @unittest.skipIf(not _MPS_AVAILABLE, "MPS not available")
    async def test_mps_stego_speed(self):
        """Měření rychlosti – ≤ 100 ms pro 1024×1024."""
        start = time.time()
        await self.detector._detect_mps(self.img_bytes)
        elapsed = (time.time() - start) * 1000
        self.assertLess(elapsed, 100)

    @unittest.skipIf(not _MPS_AVAILABLE, "MPS not available")
    async def test_mps_cache_cleared(self):
        """Ověří, že se volá torch.mps.empty_cache()."""
        with patch('torch.mps.empty_cache') as mock_cache:
            await self.detector._detect_mps(self.img_bytes)
            mock_cache.assert_called_once()

    async def test_mps_fallback(self):
        """Pokud MPS není k dispozici, použije se CPU."""
        with patch('hledac.universal.security.stego_detector.MPS_AVAILABLE', False):
            from hledac.universal.security import stego_detector
            detector = stego_detector.StatisticalStegoDetector()
            with patch.object(detector, '_detect_cpu', return_value={"score": 0.3}) as mock_cpu:
                result = await detector.detect(self.img_bytes)
                mock_cpu.assert_called_once()
                self.assertEqual(result["score"], 0.3)


# =============================================================================
# AMX sketch
# =============================================================================

class TestAMXSketch(unittest.IsolatedAsyncioTestCase):
    """Testy pro AMX akcelerovaný Count‑Mean‑Min sketch."""

    async def asyncSetUp(self):
        from hledac.universal.utils.sketches import HybridFrequencySketch
        self.sketch = HybridFrequencySketch(sketch_width=2**10, sketch_depth=3)

    @unittest.skipIf(not _MLX_AVAILABLE, "MLX not available")
    async def test_amx_sketch_vectorized(self):
        """Ověří, že _update_sketch používá vektorizované operace (mx.at)."""
        import inspect
        source = inspect.getsource(self.sketch._update_sketch)
        # Must use mx.at for vectorized updates
        self.assertIn("mx.at", source)
        # Must NOT use the old non-vectorized pattern
        self.assertNotIn("self.table[d, indices[d]]", source)

    @unittest.skipIf(not _MLX_AVAILABLE, "MLX not available")
    async def test_amx_sketch_speed(self):
        """Aktualizace 1000 položek musí být ≤ 50 ms (s MLX)."""
        items = [f"item_{i}" for i in range(1000)]
        start = time.time()
        for item in items:
            self.sketch.add(item, 1)
        elapsed = (time.time() - start) * 1000
        self.assertLess(elapsed, 50)

    async def test_amx_sketch_accuracy(self):
        """Odhad se nesmí lišit o více než 5 % od skutečnosti."""
        self.sketch.add("test", 100)
        estimate = self.sketch.estimate("test")
        self.assertAlmostEqual(estimate, 100, delta=5)

    async def test_amx_fallback(self):
        """Pokud MLX není k dispozici, použije se Python implementace."""
        from hledac.universal.utils.sketches import HybridFrequencySketch
        if _MLX_AVAILABLE:
            with patch('hledac.universal.utils.sketches.MLX_AVAILABLE', False):
                sketch = HybridFrequencySketch(sketch_width=2**10, sketch_depth=3)
                sketch.add("test", 10)
                self.assertEqual(sketch.estimate("test"), 10)
        else:
            sketch = HybridFrequencySketch(sketch_width=2**10, sketch_depth=3)
            sketch.add("test", 10)
            self.assertEqual(sketch.estimate("test"), 10)


# =============================================================================
# Spuštění
# =============================================================================

if __name__ == '__main__':
    unittest.main()
