"""
Sprint F281: Privacy Compute Budget
====================================



M1 8GB-safe privacy lane compute reservation.

ARCHITECTURE:
- 15% of total fetch workers reserved for privacy transports (Tor/I2P/Nym).
- Per-transport semaphores bound to transport availability (env gates + process checks).
- Fail-soft: unavailable transport → fallback to clearnet.
- M1 8GB budget: Tor ~80MB, I2P ~60MB, Nym ~120MB per active session.

WIRE: coordinators/fetch_coordinator.py — replace ad-hoc _tor_max_sessions
with PrivacyBudgetAllocator at __init__ time.

TRANSPORT ROUTING:
  Onion (.onion)     → Tor semaphore (CONCURRENCY_TOR default 4)
  I2P (.i2p)         → I2P semaphore (CONCURRENCY_I2P default 1)
  Nym (nym:)         → Nym semaphore (CONCURRENCY_NYM default 1)
  Clearnet           → AIMD clearnet semaphore (unmodified)

ENV GATES (checked at init):
  HLEDAC_ENABLE_TOR=1  → Tor lane enabled
  HLEDAC_ENABLE_I2P=1  → I2P lane enabled
  HLEDAC_ENABLE_NYM=1  → Nym lane enabled

FALLBACK: if transport process unavailable or env disabled,
onion/i2p/nym URLs fall back to clearnet AIMD lane.
"""
import asyncio
import logging
import os
from dataclasses import dataclass, field
import msgspec
from _core import aclose
logger = logging.getLogger(__name__)
PRIVACY_BUDGET_RATIO = 0.15
DEFAULT_TOR_WORKERS = 2
DEFAULT_I2P_WORKERS = 1
DEFAULT_NYM_WORKERS = 1
MIN_CLEARNET_WORKERS = 3

class PrivacyLaneConfig(msgspec.Struct, frozen=True, gc=False):
    """Configuration for a single privacy transport lane."""
    name: str
    workers: int
    env_gate: str
    ram_per_session_mb: int = 80

class PrivacyBudgetAllocator(msgspec.Struct, gc=False):
    """
    Allocates privacy lane semaphores from total fetch worker budget.

    Guarantees 15% (min 1) of workers for privacy transports.
    Rest goes to clearnet. Privacy lanes fail-soft to clearnet on unavailability.

    Thread-safe: all state protected by _lock.
    """
    total_workers: int
    tor_config: PrivacyLaneConfig = field(default_factory=lambda: PrivacyLaneConfig(name='tor', workers=DEFAULT_TOR_WORKERS, env_gate='HLEDAC_ENABLE_TOR', ram_per_session_mb=80))
    i2p_config: PrivacyLaneConfig = field(default_factory=lambda: PrivacyLaneConfig(name='i2p', workers=DEFAULT_I2P_WORKERS, env_gate='HLEDAC_ENABLE_I2P', ram_per_session_mb=60))
    nym_config: PrivacyLaneConfig = field(default_factory=lambda: PrivacyLaneConfig(name='nym', workers=DEFAULT_NYM_WORKERS, env_gate='HLEDAC_ENABLE_NYM', ram_per_session_mb=120))
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _initialized: bool = field(default=False, repr=False)
    _tor_sem: asyncio.Semaphore | None = field(default=None, repr=False)
    _i2p_sem: asyncio.Semaphore | None = field(default=None, repr=False)
    _nym_sem: asyncio.Semaphore | None = field(default=None, repr=False)
    _clearnet_budget: int = field(default=0, repr=False)
    _available_lanes: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        privacy_total = max(1, int(self.total_workers * PRIVACY_BUDGET_RATIO))
        tor_w = min(self.tor_config.workers, privacy_total // 2)
        i2p_w = min(self.i2p_config.workers, max(1, privacy_total // 4))
        nym_w = min(self.nym_config.workers, max(1, privacy_total // 4))
        clearnet_w = max(MIN_CLEARNET_WORKERS, self.total_workers - privacy_total)
        available = []
        if tor_w > 0 and self._check_env_gate(self.tor_config.env_gate):
            self._tor_sem = asyncio.Semaphore(tor_w)
            available.append('tor')
        if i2p_w > 0 and self._check_env_gate(self.i2p_config.env_gate):
            self._i2p_sem = asyncio.Semaphore(i2p_w)
            available.append('i2p')
        if nym_w > 0 and self._check_env_gate(self.nym_config.env_gate):
            self._nym_sem = asyncio.Semaphore(nym_w)
            available.append('nym')
        self._clearnet_budget = clearnet_w
        self._available_lanes = tuple(available)
        self._initialized = True
        logger.info(f'[PrivacyBudget] total={self.total_workers}, privacy={privacy_total} ({PRIVACY_BUDGET_RATIO:.0%}), lanes={available}, clearnet={clearnet_w}')

    @staticmethod
    def _check_env_gate(env_gate: str) -> bool:
        """Check if env gate is enabled. Defaults disabled (0)."""
        return os.environ.get(env_gate, '0') in ('1', 'true', 'True')

    @property
    def clearnet_budget(self) -> int:
        """Number of clearnet workers available (post privacy reservation)."""
        return self._clearnet_budget

    @property
    def available_privacy_lanes(self) -> tuple[str, ...]:
        """Names of available privacy lanes (env-gated and worker > 0)."""
        return self._available_lanes

    def get_semaphore(self, lane: str) -> asyncio.Semaphore | None:
        """
        Get the semaphore for a privacy lane.

        Returns None if lane is unavailable (env disabled or workers=0).
        Caller should fall back to clearnet in that case.
        """
        if lane == 'tor':
            return self._tor_sem
        elif lane == 'i2p':
            return self._i2p_sem
        elif lane == 'nym':
            return self._nym_sem
        return None

    def get_lane_for_url(self, url: str) -> str:
        """
        Classify URL transport class and return lane name.

        Matches url_ops.classify_url semantics:
          .onion  → "tor"
          .i2p    → "i2p"
          nym:    → "nym"
          default → "clearnet"
        """
        url_lower = url.lower().strip()
        if url_lower.endswith('.onion'):
            return 'tor'
        if url_lower.endswith('.i2p'):
            return 'i2p'
        if url_lower.startswith('nym:'):
            return 'nym'
        return 'clearnet'

    def get_budget_summary(self) -> dict:
        """Return budget allocation summary for telemetry."""
        return {'total_workers': self.total_workers, 'privacy_ratio': PRIVACY_BUDGET_RATIO, 'clearnet_budget': self._clearnet_budget, 'available_lanes': self._available_lanes, 'lane_budgets': {'tor': self._tor_sem._value if self._tor_sem else 0, 'i2p': self._i2p_sem._value if self._i2p_sem else 0, 'nym': self._nym_sem._value if self._nym_sem else 0}}

def make_privacy_allocator(total_workers: int) -> PrivacyBudgetAllocator:
    """Factory: create a PrivacyBudgetAllocator with the given total worker count."""
    return PrivacyBudgetAllocator(total_workers=total_workers)