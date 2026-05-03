"""
SSM reranker – malý state‑space model pro reranking kandidátů.
Vstup: (batch, seq_len, feature_dim) – sekvence kandidátů.
Výstup: (batch, seq_len) – skóre pro každého kandidáta.
Obsahuje fast path pro případ, že depthwise konvoluce je pomalá.
"""

import logging
import time

# MLX optional import — SSMReranker requires MLX to be useful
try:
    import mlx.core as mx
    import mlx.nn as nn

    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None
    nn = None

logger = logging.getLogger(__name__)


if MLX_AVAILABLE:

    class SSMBlock(nn.Module):
        """Jeden SSM blok s volitelnou depthwise conv."""
        def __init__(self, dim: int, kernel_size: int = 3, use_depthwise: bool = True):
            super().__init__()
            if use_depthwise:
                self.conv = nn.Conv1d(dim, dim, kernel_size, padding=kernel_size // 2, groups=dim)
            else:
                self.conv = nn.Conv1d(dim, dim, kernel_size, padding=kernel_size // 2, groups=1)
            self.fc1 = nn.Linear(dim, 2 * dim)
            self.fc2 = nn.Linear(dim, dim)
            self.act = nn.GELU()

        def __call__(self, x: mx.array) -> mx.array:
            residual = x
            x = self.conv(x)
            x = self.act(x)
            gate = mx.sigmoid(self.fc1(x)[..., :x.shape[-1]])
            x = gate * x
            x = self.fc2(x)
            return x + residual


    class SSMReranker(nn.Module):
        """
        Reranker model pro top‑K kandidátů (sekvenční vstup).
        Při inicializaci změří rychlost depthwise a podle výsledku zvolí fast path.
        """
        def __init__(self, feature_dim: int = 137, hidden_dim: int = 64, num_blocks: int = 2):
            super().__init__()
            self.embed = nn.Linear(feature_dim, hidden_dim)
            self.use_depthwise = self._benchmark_depthwise(hidden_dim)
            self.blocks = [SSMBlock(hidden_dim, use_depthwise=self.use_depthwise) for _ in range(num_blocks)]
            self.out_proj = nn.Linear(hidden_dim, 1)
            logger.info(f"SSMReranker initialized with depthwise={self.use_depthwise}")

        def _benchmark_depthwise(self, dim: int, seq_len: int = 50, iterations: int = 100) -> bool:
            """Změří, zda je depthwise konvoluce rychlejší než normální, pro různé délky."""
            seq_lens = [10, 20, 50]  # pokryjeme typické top_k
            depth_wins = 0
            for sl in seq_lens:
                try:
                    x = mx.random.normal((1, sl, dim))
                    conv_depth = nn.Conv1d(dim, dim, 3, padding=1, groups=dim)
                    conv_norm = nn.Conv1d(dim, dim, 3, padding=1, groups=1)

                    # Zahřívací průchod – evaluujeme výstup
                    out = conv_depth(x)
                    mx.eval(out)
                    out = conv_norm(x)
                    mx.eval(out)

                    # Měření depthwise
                    start = time.perf_counter()
                    for _ in range(iterations):
                        out = conv_depth(x)
                    mx.eval(out)
                    depth_time = time.perf_counter() - start

                    # Měření normální
                    start = time.perf_counter()
                    for _ in range(iterations):
                        out = conv_norm(x)
                    mx.eval(out)
                    norm_time = time.perf_counter() - start

                    logger.debug(f"Benchmark seq_len={sl}: depthwise={depth_time*1000:.3f} ms, normal={norm_time*1000:.3f} ms")
                    if depth_time < norm_time:
                        depth_wins += 1
                except Exception as e:
                    logger.warning(f"Benchmark failed for seq_len={sl}: {e}")
            return depth_wins > len(seq_lens) / 2   # většina hlasů

        def __call__(self, x: mx.array) -> mx.array:
            """
            x: (batch, seq_len, feature_dim)
            returns: (batch, seq_len)
            """
            x = self.embed(x)
            for block in self.blocks:
                x = block(x)
            scores = self.out_proj(x).squeeze(-1)
            return scores

        def save(self, path: str):
            mx.savez(path, **self.parameters())

        def load(self, path: str):
            params = mx.load(path)
            self.update(params)

else:
    # Stub when MLX unavailable — fail-soft so prefetch_oracle can still import
    class SSMBlock:  # type: ignore[no-redef]
        """Stub when MLX unavailable."""
        def __init__(self, *args, **kwargs):
            raise ImportError("SSMBlock requires MLX (not available)")

    class SSMReranker:  # type: ignore[no-redef]
        """Stub when MLX unavailable."""
        def __init__(self, *args, **kwargs):
            raise ImportError("SSMReranker requires MLX (not available)")
