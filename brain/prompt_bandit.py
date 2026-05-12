"""Contextual bandit (LinUCB) for prompt selection."""
import json
import time
import os
import math
import random
import logging
from pathlib import Path
from collections import defaultdict
from typing import Optional, List, Dict, Any
import asyncio

logger = logging.getLogger(__name__)


class PromptBandit:
    # Sprint 8TD: UCB1 prompt arms
    PROMPT_ARMS: List[str] = [
        "default",      # baseline
        "adversarial",  # zaměřit na threat actors
        "temporal",     # zaměřit na timeline a TTP evolution
        "technical",    # zaměřit na IOC a CVE details
        "contextual",   # inject global context z ghost_global
    ]

    def __init__(self, brain_manager=None, alpha: float = 1.0, lambda_reg: float = 0.01,
                 context_dim: int = 9, persist_path: str = None):
        self._brain = brain_manager
        self._alpha = alpha
        self._lambda = lambda_reg
        self._d = context_dim
        self._A: Dict[int, any] = {}          # A_i = lambda*I + sum x x^T
        self._b: Dict[int, any] = {}          # b_i = sum reward * x
        self._counts: Dict[int, int] = defaultdict(int)
        self._rewards: Dict[int, float] = defaultdict(float)
        self._n_variants = 0
        self._persist_path = persist_path or Path.home() / '.hledac' / 'prompt_bandit.json'
        self._save_counter = 0
        self._save_lock = asyncio.Lock()

        # A/B test state (optional)
        self._ab_test_active = False
        self._ab_test_variants = {}
        self._ab_test_start_time = None
        self._ab_test_duration = 24 * 3600

        # Sprint 8TD: UCB1 arm state
        self._arm_counts: Dict[str, int] = {a: 0 for a in self.PROMPT_ARMS}
        self._arm_rewards: Dict[str, float] = {a: 0.0 for a in self.PROMPT_ARMS}
        self._total_pulls: int = 0
        self._ucb_c: float = 1.414  # sqrt(2) = standard UCB1

        self._load()

    def _load(self):
        if self._persist_path.exists():
            try:
                import numpy as np
                with open(self._persist_path, 'r') as f:
                    data = json.load(f)

                self._counts = defaultdict(int, data.get('counts', {}))
                self._rewards = defaultdict(float, data.get('rewards', {}))

                # Sprint F234: Load UCB1 state for arm selection continuity
                arm_counts_data = data.get('arm_counts', {})
                arm_rewards_data = data.get('arm_rewards', {})
                if arm_counts_data:
                    self._arm_counts = defaultdict(int, arm_counts_data)
                if arm_rewards_data:
                    self._arm_rewards = defaultdict(float, arm_rewards_data)
                self._total_pulls = data.get('total_pulls', 0)

                # A a b načteme jako numpy array
                for k, v in data.get('A', {}).items():
                    self._A[int(k)] = np.array(v, dtype=np.float64)
                for k, v in data.get('b', {}).items():
                    self._b[int(k)] = np.array(v, dtype=np.float64)

                self._n_variants = data.get('n_variants', 0)
            except Exception as e:
                logger.warning(f"Bandit load failed: {e}")

    async def _save(self):
        """Atomický save s temp file a fsync."""
        async with self._save_lock:
            try:
                import numpy as np
                # Převedeme numpy array na seznamy pro JSON
                A_json = {str(k): v.tolist() for k, v in self._A.items()}
                b_json = {str(k): v.tolist() for k, v in self._b.items()}

                # Ensure directory exists
                self._persist_path.parent.mkdir(parents=True, exist_ok=True)

                temp = self._persist_path.with_suffix('.tmp')
                with open(temp, 'w') as f:
                    json.dump({
                        'counts': dict(self._counts),
                        'rewards': dict(self._rewards),
                        # Sprint F234: Persist UCB1 state for arm selection continuity across boots
                        'arm_counts': dict(self._arm_counts),
                        'arm_rewards': dict(self._arm_rewards),
                        'total_pulls': self._total_pulls,
                        'A': A_json,
                        'b': b_json,
                        'n_variants': self._n_variants
                    }, f)
                    f.flush()
                    os.fsync(f.fileno())
                temp.replace(self._persist_path)
            except Exception as e:
                logger.warning(f"Bandit save failed: {e}")

    def _get_context_vector(self, context: dict = None) -> list:
        """9‑dimenzionální kontextový vektor."""
        context = context or {}

        # Base features
        complexity = {"low": 0.0, "medium": 0.5, "high": 1.0}.get(context.get('complexity', 'medium'), 0.5)
        task_map = {"analysis": 0.0, "extraction": 0.5, "summarization": 1.0}
        task = task_map.get(context.get('task', 'analysis'), 0.0)
        hour = (time.localtime().tm_hour) / 23.0

        # Apple Silicon features
        thermal_state = 0.0
        on_battery = 0.0
        available_ram = 1.0
        ane_load = 0.0
        gpu_load = 0.0

        if self._brain:
            try:
                orch = getattr(self._brain, '_orch', None)
                if orch:
                    mgr = getattr(orch, '_memory_mgr', None)
                    if mgr:
                        thermal = mgr.get_thermal_state()
                        thermal_state = {'NORMAL': 0.0, 'WARM': 0.33, 'HOT': 0.66, 'CRITICAL': 1.0}.get(thermal.name, 0.0)
                        on_battery = 1.0 if mgr._on_battery_power() else 0.0

                        # ANE load estimate from metrics registry
                        if hasattr(orch, '_metrics_registry'):
                            metrics = getattr(orch._metrics_registry, '_metrics', {})
                            ane_load = metrics.get('ane_activity_estimate', 0.0)
            except Exception:
                pass

        try:
            import psutil
            available_ram = min(1.0, psutil.virtual_memory().available / (1024**3) / 8.0)
            try:
                import mlx.core as mx
                # Sprint 8AE: prefer top-level mx API with hasattr guard
                if hasattr(mx, 'get_active_memory'):
                    gpu_load = min(1.0, mx.get_active_memory() / (4 * 1024**3))
                elif hasattr(mx.metal, 'get_active_memory'):
                    gpu_load = min(1.0, mx.metal.get_active_memory() / (4 * 1024**3))
                else:
                    gpu_load = 0.0
            except Exception:
                pass
        except Exception:
            pass

        return [complexity, task, hour, thermal_state, on_battery, available_ram, ane_load, gpu_load, 1.0]

    def set_variants(self, variants: list):
        self._n_variants = len(variants)

    async def select(self, variants: list, context: dict = None) -> int:
        """Vrátí index vybrané varianty pomocí LinUCB s cold‑start randomizací."""
        if not variants:
            return -1
        self._n_variants = len(variants)

        # Cold start – náhodný výběr z nevyzkoušených
        untried = [i for i in range(self._n_variants) if self._counts.get(i, 0) == 0]
        if untried:
            return random.choice(untried)

        x = self._get_context_vector(context)
        try:
            import numpy as np
            x_np = np.array(x, dtype=np.float64)

            best_i, best_ucb = 0, -float('inf')
            for i in range(self._n_variants):
                if i not in self._A:
                    self._A[i] = self._lambda * np.eye(self._d, dtype=np.float64)
                    self._b[i] = np.zeros(self._d, dtype=np.float64)

                A_i = self._A[i]
                b_i = self._b[i]
                try:
                    theta = np.linalg.solve(A_i, b_i)
                    sigma = np.sqrt(x_np @ np.linalg.solve(A_i, x_np))
                    ucb = theta @ x_np + self._alpha * sigma
                except np.linalg.LinAlgError:
                    # fallback při numerické nestabilitě
                    ucb = self._rewards.get(i, 0) / max(1, self._counts.get(i, 1))
                if ucb > best_ucb:
                    best_ucb, best_i = ucb, i
            return best_i

        except ImportError:
            # fallback na UCB1 bez numpy
            total = sum(self._counts.values())
            ucb = [
                self._rewards.get(i, 0) / max(1, self._counts.get(i, 1))
                + self._alpha * math.sqrt(2 * math.log(total + 1) / max(1, self._counts.get(i, 1)))
                for i in range(self._n_variants)
            ]
            return max(range(self._n_variants), key=lambda i: ucb[i])

    async def update(self, idx: int, reward: float, context: dict = None):
        """Aktualizuje parametry banditu."""
        if idx < 0:
            return

        # Clip reward do [0,1]
        reward = max(0.0, min(1.0, reward))

        self._counts[idx] += 1
        self._rewards[idx] += reward
        x = self._get_context_vector(context)

        try:
            import numpy as np
            x_np = np.array(x, dtype=np.float64)
            if idx not in self._A:
                self._A[idx] = self._lambda * np.eye(self._d, dtype=np.float64)
                self._b[idx] = np.zeros(self._d, dtype=np.float64)
            self._A[idx] += np.outer(x_np, x_np)
            self._b[idx] += reward * x_np
        except ImportError:
            pass  # numpy není – pouze updatujeme counts/rewards

        self._save_counter += 1
        if self._save_counter % 10 == 0:
            task = asyncio.create_task(self._save())
            def _log_error(t):
                try:
                    t.result()
                except Exception as e:
                    logger.error(f"Bandit save failed: {e}")
            task.add_done_callback(_log_error)

    async def final_save(self):
        """Volá se při shutdown – zajistí uložení."""
        await self._save()

    # A/B test methods (optional)
    def start_ab_test(self, variant_ids: List[int], duration_hours: int = 24):
        self._ab_test_active = True
        self._ab_test_variants = {vid: {'impressions': 0, 'conversions': 0} for vid in variant_ids}
        self._ab_test_start_time = time.time()
        self._ab_test_duration = duration_hours * 3600

    def record_ab_impression(self, variant_id: int):
        if self._ab_test_active and variant_id in self._ab_test_variants:
            self._ab_test_variants[variant_id]['impressions'] += 1

    def record_ab_conversion(self, variant_id: int, reward: float):
        if self._ab_test_active and variant_id in self._ab_test_variants:
            self._ab_test_variants[variant_id]['conversions'] += reward

    def get_ab_test_results(self) -> dict:
        if not self._ab_test_active:
            return {}
        results = {}
        for vid, data in self._ab_test_variants.items():
            if data['impressions'] > 0:
                results[vid] = {
                    'impressions': data['impressions'],
                    'conversions': data['conversions'],
                    'conversion_rate': data['conversions'] / data['impressions'],
                }
        return results

    def check_ab_test_complete(self) -> Optional[int]:
        if not self._ab_test_active:
            return None
        if time.time() - self._ab_test_start_time < self._ab_test_duration:
            return None
        best_vid = None
        best_rate = 0.0
        for vid, data in self._ab_test_variants.items():
            if data['impressions'] >= 10:
                rate = data['conversions'] / data['impressions']
                if rate > best_rate:
                    best_rate = rate
                    best_vid = vid
        self._ab_test_active = False
        return best_vid

    def select_arm(self) -> str:
        """Sprint 8TD: UCB1 selection. Vrátí název ARM."""
        if self._total_pulls < len(self.PROMPT_ARMS):
            # Explore: každý arm alespoň 1×
            return self.PROMPT_ARMS[self._total_pulls]

        ucb_scores = {}
        for arm in self.PROMPT_ARMS:
            if self._arm_counts[arm] == 0:
                ucb_scores[arm] = float('inf')
            else:
                avg = self._arm_rewards[arm] / self._arm_counts[arm]
                ucb = avg + self._ucb_c * math.sqrt(
                    math.log(self._total_pulls) / self._arm_counts[arm])
                ucb_scores[arm] = ucb
        best = max(ucb_scores, key=ucb_scores.get)
        logger.debug(f"PromptBandit UCB1: selected={best}, scores={ucb_scores}")
        return best

    def update_reward(self, arm: str, fpm: float, novelty: float) -> None:
        """Sprint 8TD: Volat po každém sprintu s výsledkem."""
        reward = fpm * novelty  # kompozitní reward signal
        if arm in self._arm_counts:
            self._arm_counts[arm] += 1
            self._arm_rewards[arm] += reward
            self._total_pulls += 1
            logger.info(f"PromptBandit: arm={arm} reward={reward:.3f} "
                        f"total_pulls={self._total_pulls}")

    def get_prompt_modifier(self, arm: str) -> str:
        """Sprint 8TD: Vrátí prompt modifikaci pro daný arm."""
        modifiers = {
            "default":      "",
            "adversarial":  "\nFocus on: threat actors, TTPs, attribution evidence.",
            "temporal":     "\nFocus on: timeline, campaign evolution, date correlations.",
            "technical":    "\nFocus on: CVEs, IOCs, malware hashes, network indicators.",
            "contextual":   "\nIncorporate recurring entities from previous sprints.",
        }
        return modifiers.get(arm, "")

    def get_stats(self) -> Dict[str, Any]:
        """Sprint 8TD: Vrátit arm statistics for DuckDB persistence."""
        return {
            "arm_counts": dict(self._arm_counts),
            "arm_rewards": dict(self._arm_rewards),
            "total_pulls": self._total_pulls,
        }
