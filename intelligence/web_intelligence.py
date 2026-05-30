"""
Web Intelligence Helper — OSINT scraping and analysis utilities.

Provides a lightweight wrapper around Hledac's scraping and OSINT components
with bounded queue management and graceful degradation when optional
dependencies are unavailable.

This is a utility module, not a canonical runtime owner. All heavy
orchestration lives in the autonomous_orchestrator.
"""

import asyncio
import heapq
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import aiohttp
from hledac.universal.network.session_runtime import async_get_aiohttp_session
from hledac.universal.utils.uuid7 import new_runtime_id

# psutil je optional — nepovinný pro M1 lightweight provoz
try:
    import psutil
    _PSUTIL_ERROR: Exception | None = None
except ImportError as e:
    psutil = None  # type: ignore[assignment]
    _PSUTIL_ERROR = e

logger = logging.getLogger(__name__)

# STUB: hledac.advanced_web, hledac.intelligence.osint_reporting_generator,
# hledac.social_engineering, hledac.stealth_web_v2 deleted in prior sprints.


class IntelligenceOperationType(Enum):
    """Types of intelligence operations."""
    WEB_SCRAPING = "web_scraping"
    OSINT_COLLECTION = "osint_collection"
    THREAT_ASSESSMENT = "threat_assessment"
    VULNERABILITY_ANALYSIS = "vulnerability_analysis"
    COMPREHENSIVE_INTELLIGENCE = "comprehensive_intelligence"


class OperationStatus(Enum):
    """Operation status tracking."""
    PENDING = "pending"
    INITIALIZING = "initializing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class IntelligenceTarget:
    """Unified intelligence target configuration."""
    target_id: str
    name: str
    urls: list[str] = field(default_factory=list)
    selectors: dict[str, str] = field(default_factory=dict)
    osint_sources: list[str] = field(default_factory=list)
    operation_types: list[IntelligenceOperationType] = field(default_factory=list)
    max_depth: int = 3
    priority: str = "medium"  # low, medium, high, critical
    compliance_level: str = "strict"  # strict, moderate, permissive
    stealth_level: str = "high"  # low, medium, high, maximum


@dataclass
class TechIntelligence:
    """Tech stack intelligence inferred from job postings."""
    detected_technologies: dict[str, int]  # tech → frequency
    hiring_patterns: list[str]              # "scaling team", "greenfield"
    seniority_distribution: dict[str, int]   # junior/mid/senior counts
    inferred_pain_points: list[str]          # "performance issues"


@dataclass
class IntelligenceResult:
    """Comprehensive intelligence result."""
    operation_id: str
    target_id: str
    operation_type: IntelligenceOperationType
    status: OperationStatus
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    execution_time: float = 0.0

    # Results data
    web_data: dict[str, Any] = field(default_factory=dict)
    osint_data: dict[str, Any] = field(default_factory=dict)
    threat_assessment: dict[str, Any] = field(default_factory=dict)
    vulnerabilities: list[dict[str, Any]] = field(default_factory=list)

    # Metadata
    sources_used: list[str] = field(default_factory=list)
    confidence_score: float = 0.0
    stealth_score: float = 0.0
    requests_made: int = 0
    errors: list[str] = field(default_factory=list)

    # Performance metrics
    flashattention_accelerations: int = 0
    captcha_solved: int = 0
    detection_evasions: int = 0
    pages_processed: int = 0


class UnifiedWebIntelligence:
    """
    Web intelligence helper — OSINT scraping and threat analysis utilities.

    Provides a bounded, lazy-initialized wrapper around Hledac's optional scraping
    and OSINT components. This is a utility helper, not a canonical runtime
    owner; all heavy orchestration lives in autonomous_orchestrator.

    Key Features:
    1. Bounded queue with priority aging
    2. Lazy component initialization on first operation
    3. Graceful degradation when optional dependencies are unavailable
    4. Task ownership tracking with symmetric cleanup
    5. Memory pressure awareness for M1 8GB environments
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

        # Initialize core components (LAZY — initialize on first use)
        self.automation_orchestrator: AutomationOrchestrator | None = None
        self.intelligent_scraper: IntelligentScraper | None = None
        self.osint_reporter: OSINTReportingGenerator | None = None
        self.osint_aggregator: OSINTAggregator | None = None
        self._components_initialized: bool = False
        self._components_init_task: asyncio.Task | None = None

        # Operation tracking
        self.active_operations: dict[str, IntelligenceResult] = {}
        self._completed_operations: Ordereddict[str, IntelligenceResult] = OrderedDict()
        self._completed_operations_limit: int = self.config.get('completed_operations_limit', 1000)
        # Priority queue using heapq: (priority, counter, operation_id)
        # Priority: 0=low, 1=medium, 2=high, 3=critical (lower = higher priority)
        self.operation_queue: list[tuple] = []
        self._queue_counter = 0  # Tiebreaker for deterministic ordering

        # Queue bounds (DRIFT FIX 1: bounded + _queued_ops sync + no unbounded growth)
        self._MAX_QUEUE = 500
        self._MAX_QUEUED_OPS = 500  # mirror bound for _queued_ops dict
        self._queued_ops: dict[str, tuple[IntelligenceTarget, list[IntelligenceOperationType], IntelligenceResult]] = {}

        # Priority aging for queued operations (DRIFT FIX 2: aging task shutdown symmetry)
        self._aging_threshold_seconds = 30  # age after 30 seconds
        self._aging_interval_seconds = 5    # check every 5 seconds
        self._aging_task: asyncio.Task | None = None
        self._aging_shutdown = asyncio.Event()  # graceful exit for aging loop

        # Task ownership — operation tasks tracked for symmetric cleanup
        self._MAX_ACTIVE_TASKS = 200  # hard cap on concurrent operation tasks
        self._active_tasks: set[asyncio.Task] = set()  # owned tasks
        self._queued_op_times: dict[str, float] = {}  # operation_id -> enqueue timestamp

        # Memory budget enforcement (DRIFT FIX 3: psutil.Process lazy + NoSuchProcess cache poison)
        self._memory_limit_bytes = 512 * 1024 * 1024  # 512 MB
        self._process: psutil.Process | None = None  # lazy, created on first memory check
        self._process_initialized: bool = False
        self._process_dead: bool = False  # True once process is known-dead (avoids repeated syscall)

        # Lazy init coordination (LANDMINE FIX 4: race condition on _components_initialized)
        self._init_lock = asyncio.Lock()
        self._components_init_error: Exception | None = None  # surface init failures

        # Performance metrics
        self.metrics = {
            'total_operations': 0,
            'completed_operations': 0,
            'failed_operations': 0,
            'average_execution_time': 0.0,
            'total_pages_processed': 0,
            'total_captcha_solved': 0,
            'total_detections_evaded': 0,
            'flashattention_usage': 0,
            'success_rate': 0.0,
            'stealth_score_average': 0.0
        }

        # Configuration
        self.max_concurrent_operations = self.config.get('max_concurrent_operations', 5)
        self.enable_flashattention = self.config.get('enable_flashattention', True)
        self.enable_osint = self.config.get('enable_osint', True)
        self.enable_stealth = self.config.get('enable_stealth', True)

        # NO fire-and-forget tasks — components initialized lazily on first operation
        # NO background tasks started in __init__ — prevents orphaned tasks on GC

        logger.info("🧠 Unified Web Intelligence System created (lazy init mode)")
        logger.info("📊 completed_operations bounded to %d entries", self._completed_operations_limit)

    @property
    def is_degraded(self) -> bool:
        """True pokud modul běží v degraded mode (chybí volitelné komponenty)."""
        return _IMPORT_ERROR is not None

    @property
    def degradation_reason(self) -> str | None:
        """Důvod degraded módu, pokud existuje."""
        return str(_IMPORT_ERROR) if _IMPORT_ERROR else None

    @property
    def queue_health(self) -> dict[str, Any]:
        """Read-only seam: queue pressure and aging status at a glance."""
        return {
            'queued_count': len(self.operation_queue),
            'queue_limit': self._MAX_QUEUE,
            'queue_pressure_pct': round(len(self.operation_queue) / self._MAX_QUEUE * 100, 1),
            'aging_task_alive': (
                self._aging_task is not None
                and not self._aging_task.done()
            ),
            'oldest_queued_seconds': (
                round(time.time() - min(self._queued_op_times.values()), 1)
                if self._queued_op_times else None
            ),
        }

    @property
    def memory_posture(self) -> dict[str, Any]:
        """Read-only seam: memory pressure state for M1 8GB."""
        try:
            # Lazy init psutil.Process if not yet initialized
            if psutil is not None and not self._process_initialized and not self._process_dead:
                try:
                    self._process = psutil.Process()
                    self._process_initialized = True
                except psutil.NoSuchProcess:
                    self._process_dead = True
                    return {'rss_mb': None, 'limit_mb': self._memory_limit_bytes / 1024 / 1024, 'error': 'process_dead'}
            rss_mb = self._process.memory_info().rss / 1024 / 1024 if (self._process and not self._process_dead) else None
            limit_mb = self._memory_limit_bytes / 1024 / 1024
            result = {
                'rss_mb': round(rss_mb, 1) if rss_mb else None,
                'limit_mb': round(limit_mb, 1),
                'pressure_pct': round(rss_mb / limit_mb * 100, 1) if rss_mb else None,
                'psutil_available': psutil is not None,
                'process_dead': self._process_dead,
            }
            if self._process_dead:
                result['error'] = 'process_dead'
            return result
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            if not self._process_dead:
                self._process_dead = True
            return {'rss_mb': None, 'limit_mb': self._memory_limit_bytes / 1024 / 1024,
                    'error': 'unavailable' if not self._process_dead else 'process_dead'}

    @property
    def active_posture(self) -> dict[str, Any]:
        """Read-only seam: active vs queued posture."""
        return {
            'active_count': len(self.active_operations),
            'active_limit': self.max_concurrent_operations,
            'is_queued': len(self.active_operations) >= self.max_concurrent_operations,
            'components_initialized': self._components_initialized,
            'init_error': str(self._components_init_error) if self._components_init_error else None,
        }

    @property
    def completed_operations(self) -> dict[str, IntelligenceResult]:
        """Backward-compatible accessor for completed_operations (read-only copy)."""
        return dict(self._completed_operations)

    @property
    def completed_count(self) -> int:
        """Read-only count of completed operations (bounded)."""
        return len(self._completed_operations)

    def _add_completed_operation(self, operation_id: str, result: IntelligenceResult) -> None:
        """Add operation to completed_operations with bounded FIFO eviction.

        Eviction policy: oldest (first-inserted) entries are removed
        when the limit is exceeded.
        """
        # F191C: dedup guard — update-existing must NOT trigger FIFO eviction
        if operation_id in self._completed_operations:
            self._completed_operations[operation_id] = result
            return
        # Evict oldest if at limit (only for NEW entries)
        if len(self._completed_operations) >= self._completed_operations_limit:
            evicted_id, _ = self._completed_operations.popitem(last=False)
            logger.debug(
                "intel.webintel: completed_operations eviction (FIFO, limit=%d): "
                "evicted operation_id=%s", self._completed_operations_limit, evicted_id
            )
        self._completed_operations[operation_id] = result

    async def _initialize_components(self):
        """Initialize all intelligence components."""
        try:
            # Initialize automation orchestrator
            if AutomationOrchestrator:
                self.automation_orchestrator = AutomationOrchestrator(
                    self.config.get('automation_orchestrator', {})
                )
                logger.info("✅ Automation orchestrator initialized")

            # Initialize intelligent scraper
            if IntelligentScraper:
                scraper_config = ScrapingConfig(
                    enable_flashattention=self.enable_flashattention,
                    auto_solve_captcha=True,
                    respect_robots_txt=True,
                    max_concurrent_requests=self.max_concurrent_operations
                )
                self.intelligent_scraper = IntelligentScraper(scraper_config)
                logger.info("✅ Intelligent scraper initialized")

            # Initialize OSINT reporter
            if OSINTReportingGenerator:
                self.osint_reporter = OSINTReportingGenerator(
                    self.config.get('osint_reporter', {})
                )
                logger.info("✅ OSINT reporter initialized")

            # Initialize OSINT aggregator
            if OSINTAggregator and self.enable_osint:
                osint_config = OSINTConfig(
                    max_concurrent_requests=self.max_concurrent_operations,
                    compliance_mode="strict",
                    enable_caching=True
                )
                self.osint_aggregator = OSINTAggregator(osint_config.__dict__)
                await self.osint_aggregator.initialize()
                logger.info("✅ OSINT aggregator initialized")

            logger.info("🎯 All components initialized successfully")

        except Exception as e:
            logger.error(f"❌ Component initialization failed: {e}")

    async def execute_intelligence_operation(self, target: IntelligenceTarget,
                                           operation_types: list[IntelligenceOperationType] | None = None) -> str:
        """
        Execute comprehensive intelligence operation on target.

        Args:
            target: Intelligence target configuration
            operation_types: Types of operations to perform (default: all available)

        Returns:
            Operation ID for tracking results
        """
        operation_id = new_runtime_id()
        operation_types = operation_types or target.operation_types

        if not operation_types:
                operation_types = [IntelligenceOperationType.WEB_SCRAPING]

        # Initialize operation result
        result = IntelligenceResult(
            operation_id=operation_id,
            target_id=target.target_id,
            operation_type=IntelligenceOperationType.COMPREHENSIVE_INTELLIGENCE if len(operation_types) > 1 else operation_types[0],
            status=OperationStatus.PENDING
        )

        self.metrics['total_operations'] += 1

        # Lazy initialization — spustit při první operaci, ne při __init__
        await self._ensure_components_initialized()

        # Map priority string to numeric priority (lower = higher priority)
        priority_map = {"low": 3, "medium": 2, "high": 1, "critical": 0}

        # Memory budget enforcement — lazy psutil.Process() with dead-process cache
        try:
            if psutil is not None and not self._process_initialized and not self._process_dead:
                try:
                    self._process = psutil.Process()
                    self._process_initialized = True
                except psutil.NoSuchProcess:
                    self._process_dead = True
                    current_rss = 0
            current_rss = self._process.memory_info().rss if (self._process and not self._process_dead) else 0
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            self._process_dead = True
            current_rss = 0  # treat as unknown memory state = don't block
        memory_exceeded = current_rss > self._memory_limit_bytes

        # HARD BOUND: reject new operations if queue itself is at limit
        if len(self.operation_queue) >= self._MAX_QUEUE:
            raise RuntimeError(
                f"web_intelligence queue FULL ({self._MAX_QUEUE}), "
                f"cannot accept operation {operation_id}"
            )

        # HARD BOUND: reject if _queued_ops mirror is at limit (prevents unbounded dict growth)
        if len(self._queued_ops) >= self._MAX_QUEUED_OPS:
            raise RuntimeError(
                f"web_intelligence _queued_ops FULL ({self._MAX_QUEUED_OPS}), "
                f"cannot accept operation {operation_id}"
            )

        # Add to queue if at capacity or memory exceeded (priority-aware)
        if len(self.active_operations) >= self.max_concurrent_operations or memory_exceeded:
            priority = priority_map.get(target.priority, 2)
            self._queue_counter += 1
            # Push to heap: (priority, counter, operation_id)
            heapq.heappush(self.operation_queue, (priority, self._queue_counter, operation_id))
            # Store target, operation_types, and result for later execution
            self._queued_ops[operation_id] = (target, operation_types, result)
            # Store enqueue time for aging
            self._queued_op_times[operation_id] = time.time()
            if memory_exceeded:
                logger.warning(f"⏳ Operation {operation_id} queued due to memory pressure ({current_rss / 1024 / 1024:.1f} MB)")
            else:
                logger.info(f"⏳ Operation {operation_id} queued (priority={target.priority})")
            return operation_id

        # Execute operation asynchronously
        self.active_operations[operation_id] = result
        self._track_task(asyncio.create_task(self._execute_operation_async(target, operation_types, operation_id), name="web_intelligence:execute_operation"))

        return operation_id

    async def _execute_operation_async(self, target: IntelligenceTarget,
                                      operation_types: list[IntelligenceOperationType],
                                      operation_id: str) -> None:
        """Execute intelligence operation asynchronously."""
        result = self.active_operations[operation_id]
        result.status = OperationStatus.INITIALIZING

        try:
            logger.info(f"🚀 Starting intelligence operation: {operation_id}")
            start_time = time.time()

            # Execute each operation type
            for op_type in operation_types:
                await self._execute_operation_type(result, target, op_type)

            # Calculate final metrics
            result.execution_time = time.time() - start_time
            result.completed_at = time.time()
            result.status = OperationStatus.COMPLETED

            # Update global metrics
            self.metrics['completed_operations'] += 1
            self.metrics['total_pages_processed'] += result.pages_processed
            self.metrics['total_captcha_solved'] += result.captcha_solved
            self.metrics['total_detections_evaded'] += result.detection_evasions
            self.metrics['flashattention_usage'] += result.flashattention_accelerations
            self._update_success_rate()

            logger.info(f"✅ Operation {operation_id} completed in {result.execution_time:.2f}s")

        except Exception as e:
            result.status = OperationStatus.FAILED
            result.errors.append(str(e))
            result.execution_time = time.time() - start_time
            result.completed_at = time.time()

            self.metrics['failed_operations'] += 1
            self._update_success_rate()

            logger.error(f"❌ Operation {operation_id} failed: {e}")

        finally:
            # Move to completed (bounded, FIFO eviction)
            self._add_completed_operation(operation_id, result)
            self.active_operations.pop(operation_id, None)

            # Process queued operations (Fix 0)
            await self._process_next_queued_operation()

    async def _process_next_queued_operation(self) -> None:
        """Process the next queued operation after current one completes."""
        if not self.operation_queue:
            return
        _, _, operation_id = heapq.heappop(self.operation_queue)
        if operation_id not in self._queued_ops:
            # Prune orphan from heap — already popped but not in _queued_ops
            return
        target, op_types, result = self._queued_ops.pop(operation_id)
        # Proactive stale eviction: keep _queued_ops bounded at all times.
        # Exclude operation_id (just dequeued) so it is never flagged as stale.
        # Only run when dict is getting large to avoid O(n) on every dequeue
        if len(self._queued_ops) > self._MAX_QUEUED_OPS // 2:
            queued_ids = {oid for _, _, oid in self.operation_queue}
            stale = [k for k in self._queued_ops if k not in queued_ids and k != operation_id]
            for k in stale:
                self._queued_ops.pop(k, None)
                self._queued_op_times.pop(k, None)
        # Remove enqueue time for the dequeued op
        self._queued_op_times.pop(operation_id, None)
        # Place result where _execute_operation_async expects it
        self.active_operations[operation_id] = result
        self._track_task(asyncio.create_task(self._execute_operation_async(target, op_types, operation_id), name="web_intelligence:process_queued"))
        logger.info(f"⏭️ Processing queued operation: {operation_id}")

    async def _ensure_components_initialized(self) -> None:
        """Lazy initialization — spustí komponenty a aging task pouze jednou při první operaci.

        Uses lock to prevent race condition when multiple operations race to init.
        """
        if self._components_initialized:
            return

        async with self._init_lock:
            # Double-check after acquiring lock
            if self._components_initialized:
                return
            try:
                await self._initialize_components()
                # Start aging task AFTER successful init — don't orphan it on failure
                if self._aging_task is None:
                    self._aging_task = asyncio.create_task(self._age_queued_priorities(), name="web_intelligence:aging_loop")
                self._components_initialized = True
            except Exception as e:
                self._components_init_error = e
                self._components_initialized = True  # mark done even on failure — don't retry
                raise

    # -------------------------------------------------------------------------
    # Task ownership seam
    # -------------------------------------------------------------------------

    def _track_task(self, task: asyncio.Task) -> None:
        """Register an owned operation task. Silently drops if at capacity."""
        if len(self._active_tasks) >= self._MAX_ACTIVE_TASKS:
            logger.warning(
                "web_intelligence: _active_tasks at capacity (%d), dropping task tracking",
                self._MAX_ACTIVE_TASKS
            )
            return
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    @property
    def task_posture(self) -> dict[str, int]:
        """Read-only snapshot of task ownership state."""
        return {
            'active_operations': len(self.active_operations),
            'owned_tasks': len(self._active_tasks),
            'aging_task_alive': self._aging_task is not None and not self._aging_task.done(),
            'max_ownership': self._MAX_ACTIVE_TASKS,
        }

    async def _age_queued_priorities(self) -> None:
        """Age queued operations to improve priority over time.

        HARD EXIT: waits on shutdown event so task terminates immediately on cleanup.
        """
        while True:
            try:
                await asyncio.wait_for(
                    self._aging_shutdown.wait(),
                    timeout=self._aging_interval_seconds
                )
                # shutdown event set — exit gracefully
                break
            except TimeoutError:
                pass  # normal tick
            except asyncio.CancelledError:
                # Task was cancelled externally — exit immediately without processing
                break
            if not self.operation_queue:
                continue
            now = time.time()
            new_heap = []
            for priority, counter, op_id in self.operation_queue:
                if op_id in self._queued_op_times:
                    elapsed = now - self._queued_op_times[op_id]
                    if elapsed > self._aging_threshold_seconds:
                        increments = int(elapsed / self._aging_threshold_seconds)
                        priority = max(0, priority - increments)  # lower number = higher priority
                new_heap.append((priority, counter, op_id))
            heapq.heapify(new_heap)
            self.operation_queue = new_heap

    async def _execute_operation_type(self, result: IntelligenceResult,
                                    target: IntelligenceTarget,
                                    op_type: IntelligenceOperationType) -> None:
        """Execute specific operation type."""
        try:
            if op_type == IntelligenceOperationType.WEB_SCRAPING:
                    await self._execute_web_scraping(result, target)
            elif op_type == IntelligenceOperationType.OSINT_COLLECTION:
                    await self._execute_osint_collection(result, target)
            elif op_type == IntelligenceOperationType.THREAT_ASSESSMENT:
                    await self._execute_threat_assessment(result, target)
            elif op_type == IntelligenceOperationType.VULNERABILITY_ANALYSIS:
                    await self._execute_vulnerability_analysis(result, target)
            elif op_type == IntelligenceOperationType.COMPREHENSIVE_INTELLIGENCE:
                # Execute all operation types in parallel (Fix 5)
                tasks = [
                    self._execute_web_scraping(result, target),
                    self._execute_osint_collection(result, target),
                    self._execute_threat_assessment(result, target),
                    self._execute_vulnerability_analysis(result, target),
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            result.errors.append(f"{op_type.value} failed: {str(e)}")
            logger.error(f"❌ {op_type.value} operation failed: {e}")

    async def _execute_web_scraping(self, result: IntelligenceResult,
                                   target: IntelligenceTarget) -> None:
        """Execute web scraping operations."""
        if not self.intelligent_scraper or not target.urls:
                    return

        logger.info(f"🕷️ Executing web scraping for {target.name}")

        scraped_data = {}
        pages_processed = 0

        for url in target.urls:
            try:
                # Create scraping target
                scrape_target = ScrapingTarget(
                    url=url,
                    selectors=target.selectors,
                    max_pages=target.max_depth
                )

                # Scrape with intelligent scraper
                scrape_result = await self.intelligent_scraper.scrape_target(scrape_target)

                if scrape_result.success:
                    scraped_data[url] = scrape_result.data
                    result.requests_made += scrape_result.requests_made
                    result.stealth_score = max(result.stealth_score, scrape_result.metadata.get('stealth_score', 0))

                    if scrape_result.captcha_solved:
                        result.captcha_solved += 1

                    pages_processed += 1
                    result.sources_used.append(f"scraped:{url}")
                else:
                    result.errors.append(f"Failed to scrape {url}: {scrape_result.error_message}")

            except Exception as e:
                result.errors.append(f"Web scraping error for {url}: {str(e)}")

        result.web_data = scraped_data
        result.pages_processed += pages_processed

    async def _execute_osint_collection(self, result: IntelligenceResult,
                                      target: IntelligenceTarget) -> None:
        """Execute OSINT collection operations."""
        if not self.osint_aggregator or not target.osint_sources:
                    return

        logger.info(f"🔍 Executing OSINT collection for {target.name}")

        osint_data = {}

        # Use target name as identifier for OSINT
        target_identifier = target.name

        try:
            # Gather intelligence from configured sources
            profile = await self.osint_aggregator.gather_intelligence(
                target_identifier,
                sources=target.osint_sources
            )

            osint_data = {
                'personal_info': profile.personal_info,
                'professional_info': profile.professional_info,
                'social_media': profile.social_media,
                'contact_info': profile.contact_info,
                'relationships': profile.relationships,
                'interests': profile.interests,
                'confidence_score': profile.confidence_score,
                'data_sources': profile.data_sources
            }

            result.confidence_score = max(result.confidence_score, profile.confidence_score)
            result.sources_used.extend(profile.data_sources)

        except Exception as e:
            result.errors.append(f"OSINT collection failed: {str(e)}")

        result.osint_data = osint_data

    async def _execute_threat_assessment(self, result: IntelligenceResult,
                                        target: IntelligenceTarget) -> None:
        """Execute threat assessment."""
        logger.info(f"⚠️ Executing threat assessment for {target.name}")

        threat_assessment = {
            'threat_level': 'low',
            'confidence': 0.0,
            'risk_factors': [],
            'mitigation_strategies': []
        }

        try:
            # Analyze web data for threats
            if result.web_data:
                # Look for security indicators
                security_indicators = self._analyze_security_indicators(result.web_data)
                threat_assessment['security_analysis'] = security_indicators

            # Analyze OSINT data for threats
            if result.osint_data:
                # Analyze personal and professional information
                personal_threats = self._analyze_personal_threats(result.osint_data)
                threat_assessment['personal_threats'] = personal_threats

            # Calculate overall threat level
            threat_score = self._calculate_threat_score(threat_assessment)
            threat_assessment['threat_score'] = threat_score
            threat_assessment['threat_level'] = self._score_to_threat_level(threat_score)
            threat_assessment['confidence'] = result.confidence_score

        except Exception as e:
            result.errors.append(f"Threat assessment failed: {str(e)}")

        result.threat_assessment = threat_assessment

    async def _execute_vulnerability_analysis(self, result: IntelligenceResult,
                                            target: IntelligenceTarget) -> None:
        """Execute vulnerability analysis."""
        logger.info(f"🔒 Executing vulnerability analysis for {target.name}")

        vulnerabilities = []

        try:
            # Analyze web scraping results for vulnerabilities
            if result.web_data:
                web_vulns = self._analyze_web_vulnerabilities(result.web_data)
                vulnerabilities.extend(web_vulns)

            # Analyze OSINT data for personal vulnerabilities
            if result.osint_data:
                personal_vulns = self._analyze_personal_vulnerabilities(result.osint_data)
                vulnerabilities.extend(personal_vulns)

        except Exception as e:
            result.errors.append(f"Vulnerability analysis failed: {str(e)}")

        result.vulnerabilities = vulnerabilities

    def _analyze_security_indicators(self, web_data: dict[str, Any]) -> dict[str, Any]:
        """Analyze web data for security indicators."""
        indicators = {
            'ssl_certificates': [],
            'security_headers': [],
            'vulnerability_patterns': [],
            'suspicious_content': []
        }

        # This would implement actual security analysis
        # For now, return placeholder

        return indicators

    def _analyze_personal_threats(self, osint_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Analyze OSINT data for personal threats."""
        threats = []

        # Check for common threat indicators
        if osint_data.get('social_media'):
            # Analyze social media exposure
            exposure_risk = len(osint_data['social_media'])
            if exposure_risk > 5:
                threats.append({
                    'type': 'high_social_exposure',
                    'severity': 'medium',
                    'description': f'High social media exposure ({exposure_risk} platforms)'
                })

        if osint_data.get('contact_info'):
            # Check for exposed contact information
            if 'email' in osint_data['contact_info']:
                    threats.append({
                    'type': 'email_exposure',
                    'severity': 'low',
                    'description': 'Email address exposed in public records'
                })

            return threats

    def _calculate_threat_score(self, threat_assessment: dict[str, Any]) -> float:
        """Calculate overall threat score."""
        score = 0.0

        # Score from security analysis
        if 'security_analysis' in threat_assessment:
            score += threat_assessment['security_analysis'].get('risk_score', 0) * 0.3

        # Score from personal threats
        if 'personal_threats' in threat_assessment:
            for threat in threat_assessment['personal_threats']:
                severity_weights = {'low': 0.1, 'medium': 0.3, 'high': 0.7}
                score += severity_weights.get(threat.get('severity', 'low'), 0.1)

        return min(1.0, score)

    def _score_to_threat_level(self, score: float) -> str:
        """Convert threat score to threat level."""
        if score >= 0.7:
            return 'critical'
        elif score >= 0.5:
            return 'high'
        elif score >= 0.3:
            return 'medium'
        else:
            return 'low'

    def _analyze_web_vulnerabilities(self, web_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Analyze web data for vulnerabilities."""
        vulnerabilities = []

        # Look for common web vulnerabilities
        for url, data in web_data.items():
            # Check for exposed credentials, forms, etc.
            if isinstance(data, dict):
                if 'forms' in data:
                    vulnerabilities.append({
                        'type': 'exposed_forms',
                        'url': url,
                        'severity': 'medium',
                        'description': 'Forms detected without proper protection'
                    })

        return vulnerabilities

    def _analyze_personal_vulnerabilities(self, osint_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Analyze OSINT data for personal vulnerabilities."""
        vulnerabilities = []

        # Check for exposed personal information
        if osint_data.get('personal_info'):
            vulnerabilities.append({
                'type': 'personal_info_exposure',
                'severity': 'low',
                'description': 'Personal information available in public records'
            })

        return vulnerabilities

    def _update_success_rate(self) -> None:
        """Update operation success rate."""
        total = self.metrics['total_operations']
        if total > 0:
            self.metrics['success_rate'] = (self.metrics['completed_operations'] / total) * 100

    async def get_operation_status(self, operation_id: str) -> dict[str, Any] | None:
        """Get status of a specific operation."""
        operation = self.active_operations.get(operation_id) or self._completed_operations.get(operation_id)
        if not operation:
            return None

        return {
            'operation_id': operation.operation_id,
            'target_id': operation.target_id,
            'operation_type': operation.operation_type.value,
            'status': operation.status.value,
            'started_at': operation.started_at,
            'completed_at': operation.completed_at,
            'execution_time': operation.execution_time,
            'confidence_score': operation.confidence_score,
            'stealth_score': operation.stealth_score,
            'sources_used': operation.sources_used,
            'requests_made': operation.requests_made,
            'pages_processed': operation.pages_processed,
            'captcha_solved': operation.captcha_solved,
            'detection_evasions': operation.detection_evasions,
            'errors': operation.errors
        }

    async def get_operation_results(self, operation_id: str, format: str = "json") -> dict[str, Any]:
        """Get comprehensive operation results."""
        operation = self._completed_operations.get(operation_id)
        if not operation:
                raise ValueError(f"Operation not found: {operation_id}")

        results = {
            'operation_metadata': {
                'operation_id': operation.operation_id,
                'target_id': operation.target_id,
                'operation_type': operation.operation_type.value,
                'status': operation.status.value,
                'execution_time': operation.execution_time,
                'timestamp': operation.completed_at
            },
            'intelligence_data': {
                'web_scraping': operation.web_data,
                'osint_collection': operation.osint_data,
                'threat_assessment': operation.threat_assessment,
                'vulnerability_analysis': {
                    'vulnerabilities': operation.vulnerabilities,
                    'total_count': len(operation.vulnerabilities),
                    'high_risk_count': len([v for v in operation.vulnerabilities if v.get('severity') == 'high'])
                }
            },
            'performance_metrics': {
                'requests_made': operation.requests_made,
                'pages_processed': operation.pages_processed,
                'flashattention_accelerations': operation.flashattention_accelerations,
                'captcha_solved': operation.captcha_solved,
                'detection_evasions': operation.detection_evasions,
                'stealth_score': operation.stealth_score,
                'confidence_score': operation.confidence_score
            },
            'sources_and_metadata': {
                'data_sources_used': operation.sources_used,
                'errors_encountered': operation.errors
            }
        }

        if format == "json":
            return results
        else:
            # Could add other formats (html, pdf, etc.)
            return results

    def get_system_metrics(self) -> dict[str, Any]:
        """Get comprehensive system metrics."""
        return {
            'operations': {
                'total': self.metrics['total_operations'],
                'completed': self.metrics['completed_operations'],
                'failed': self.metrics['failed_operations'],
                'active': len(self.active_operations),
                'queued': len(self.operation_queue),
                'success_rate': self.metrics['success_rate']
            },
            'performance': {
                'average_execution_time': self.metrics['average_execution_time'],
                'total_pages_processed': self.metrics['total_pages_processed'],
                'total_captcha_solved': self.metrics['total_captcha_solved'],
                'total_detections_evaded': self.metrics['total_detections_evaded'],
                'flashattention_usage': self.metrics['flashattention_usage']
            },
            'components': {
                'automation_orchestrator': self.automation_orchestrator is not None,
                'intelligent_scraper': self.intelligent_scraper is not None,
                'osint_reporter': self.osint_reporter is not None,
                'osint_aggregator': self.osint_aggregator is not None
            },
            'configuration': {
                'max_concurrent_operations': self.max_concurrent_operations,
                'flashattention_enabled': self.enable_flashattention,
                'osint_enabled': self.enable_osint,
                'stealth_enabled': self.enable_stealth
            },
            'health': {
                'is_degraded': self.is_degraded,
                'degradation_reason': self.degradation_reason,
                'psutil_available': psutil is not None
            }
        }

    # -------------------------------------------------------------------------
    # Job Posting Tech Inference
    # -------------------------------------------------------------------------

    # Lazy spaCy / PhraseMatcher — only loaded when first needed
    _spacy_nlp = None
    _phrasematcher = None
    _SPACY_AVAILABLE = False

    # Tech keyword sets for word-boundary regex extraction
    _TECH_KEYWORDS: dict[str, set[str]] = {
        "language": {
            "python", "javascript", "typescript", "java", "go", "rust", "c++", "c#",
            "ruby", "php", "swift", "kotlin", "scala", "r", "matlab", "perl",
            "elixir", "clojure", "haskell", "dart", "lua", "shell", "bash",
        },
        "framework": {
            "react", "angular", "vue", "svelte", "next.js", "django", "flask",
            "fastapi", "spring", "rails", "laravel", "express", "nestjs", "gin",
            "fiber", "playwright", "selenium", "cypress", "pytest", "junit",
        },
        "infrastructure": {
            "kubernetes", "docker", "terraform", "ansible", "aws", "gcp", "azure",
            "k8s", "helm", "istio", "envoy", "nginx", "traefik",
            "linux", "unix", "windows server", "active directory",
        },
        "database": {
            "postgresql", "postgres", "mysql", "mongodb", "redis", "elasticsearch",
            "kafka", "rabbitmq", "graphql", "sqlite", "mariadb", "dynamodb",
            "cassandra", "couchbase", "neo4j", "influxdb", "timescale",
        },
        "ai_ml": {
            "tensorflow", "pytorch", "jax", "sklearn", "pandas", "numpy",
            "opencv", "pillow", "transformers", "hugging face", "langchain",
            "llamaindex", "crewai", "mxnet", "chainlit",
        },
        "observability": {
            "prometheus", "grafana", "datadog", "new relic", "sentry", "elk",
            "splunk", "cloudwatch", "stackdriver", "jaeger", "zipkin", "opentelemetry",
        },
        "cicd": {
            "github actions", "gitlab ci", "jenkins", "circleci", "travis",
            "argocd", "spinnaker", "tekton", "github", "bitbucket", "jira",
        },
    }

    # Hiring pattern phrases
    _HIRING_PATTERNS: list[str] = [
        "scaling team", "building from scratch", "greenfield", "high growth",
        "Series A", "Series B", "Series C", "IPO", "hypergrowth",
        "remote first", "async first", "distributed team", "international team",
        "lead engineer", "staff engineer", "principal engineer", "architect",
        "tech lead", "engineering manager", "senior", "junior", "mid-level",
    ]

    # Pain point indicators
    _PAIN_POINT_PATTERNS: list[str] = [
        "performance issues", "legacy code", "technical debt", "migration",
        "scalability challenges", "bottleneck", "outdated", "legacy system",
        "tech stack modernization", "re-architecting", "monolith", "spaghetti",
    ]

    @classmethod
    def _get_spacy_matcher(cls):
        """Lazy spaCy PhraseMatcher initialization."""
        if cls._spacy_nlp is not None:
            return cls._spacy_nlp, cls._phrasematcher
        try:
            import spacy
            from spacy.matcher import PhraseMatcher

            cls._spacy_nlp = spacy.load("en_core_web_sm")
            # Build patterns from tech keywords
            all_techs = []
            for kw_set in cls._TECH_KEYWORDS.values():
                all_techs.extend(kw_set)
            # Use nlp.make_doc for efficient pattern creation
            cls._phrasematcher = PhraseMatcher(cls._spacy_nlp.vocab, attr="TEXT")
            for tech in set(all_techs):
                cls._phrasematcher.add(tech, [cls._spacy_nlp.make_doc(tech)])
            cls._SPACY_AVAILABLE = True
            return cls._spacy_nlp, cls._phrasematcher
        except Exception:
            cls._SPACY_AVAILABLE = False
            return None, None

    def _extract_tech_regex(self, text: str) -> dict[str, int]:
        """Extract tech keywords using word-boundary regex (spaCy fallback)."""
        import re

        found: dict[str, int] = {}
        for _category, keywords in self._TECH_KEYWORDS.items():
            for kw in keywords:
                # Word boundary match (case-insensitive)
                pattern = r"\b" + re.escape(kw) + r"\b"
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    found[kw] = found.get(kw, 0) + len(matches)
        return found

    def _extract_tech_spacy(self, text: str) -> dict[str, int]:
        """Extract tech keywords using spaCy PhraseMatcher."""
        nlp, matcher = self._get_spacy_matcher()
        if nlp is None or matcher is None:
            return self._extract_tech_regex(text)
        doc = nlp(text[:50000])  # cap at 50k chars for memory
        found: dict[str, int] = {}
        matches = matcher(doc)
        for match_id, _start, _end in matches:
            tech = nlp.vocab.strings[match_id]
            found[tech] = found.get(tech, 0) + 1
        return found

    def _normalize_seniority(self, text: str) -> dict[str, int]:
        """Infer seniority distribution from job posting text."""
        import re

        text_lower = text.lower()
        counts: dict[str, int] = {"junior": 0, "mid": 0, "senior": 0}

        # Junior indicators
        junior_patterns = [
            r"\bjunior\b", r"\bentry.level\b", r"\bentry level\b", r"\bgraduate\b",
            r"\bintern\b", r"\btrainee\b", r"\bassociate\b", r"\bnew grad\b",
        ]
        for pat in junior_patterns:
            counts["junior"] += len(re.findall(pat, text_lower))

        # Mid-level indicators
        mid_patterns = [
            r"\bmid.level\b", r"\bmid level\b", r"\bintermediate\b",
            r"\b2-5 year\b", r"\b3+ year\b", r"\bexperience\b",
        ]
        for pat in mid_patterns:
            counts["mid"] += len(re.findall(pat, text_lower))

        # Senior indicators
        senior_patterns = [
            r"\bsenior\b", r"\bstaff\b", r"\bprincipal\b", r"\blead\b",
            r"\barchitect\b", r"\b5\+ year\b", r"\b7\+ year\b",
            r"\bengineering manager\b", r"\btech lead\b",
        ]
        for pat in senior_patterns:
            counts["senior"] += len(re.findall(pat, text_lower))

        return counts

    def _extract_hiring_patterns(self, text: str) -> list[str]:
        """Detect hiring patterns in job posting text."""
        import re

        text_lower = text.lower()
        found = []
        for pattern in self._HIRING_PATTERNS:
            if re.search(r"\b" + re.escape(pattern) + r"\b", text_lower):
                found.append(pattern)
        return found[:10]  # cap at 10 patterns

    def _extract_pain_points(self, text: str) -> list[str]:
        """Detect inferred pain points from job posting text."""
        import re

        text_lower = text.lower()
        found = []
        for pattern in self._PAIN_POINT_PATTERNS:
            if re.search(r"\b" + re.escape(pattern) + r"\b", text_lower):
                found.append(pattern)
        return found[:10]  # cap at 10 pain points

    async def infer_tech_from_jobs(self, entity_name: str) -> TechIntelligence:
        """
        Infer technology stack from job postings across multiple sources.

        Sources:
        - Indeed RSS: https://www.indeed.com/rss?q={entity_name}+engineer
        - Hacker News "Who is Hiring": HN API topstories.json filtered monthly
        - Remoteok.com API: https://remoteok.io/api?tag={entity_name}

        Args:
            entity_name: Company/entity name to search job postings for

        Returns:
            TechIntelligence with detected_technologies, hiring_patterns,
            seniority_distribution, and inferred_pain_points
        """
        import re

        all_text_parts: list[str] = []
        tech_counts: dict[str, int] = {}
        seniority_totals: dict[str, int] = {"junior": 0, "mid": 0, "senior": 0}
        hiring_patterns_found: set[str] = set()
        pain_points_found: set[str] = set()

        async def fetch_indeed_jobs() -> None:
            """Fetch job postings from Indeed RSS."""
            try:
                import urllib.parse

                encoded_q = urllib.parse.quote_plus(f"{entity_name} engineer")
                url = f"https://www.indeed.com/rss?q={encoded_q}"
                session = await async_get_aiohttp_session()
                async with asyncio.timeout(15.0):
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            return
                        raw = await resp.text()
                        # Streaming XML parse — defusedxml primary, stdlib fallback
                        try:
                            import defusedxml.ElementTree as ET
                        except ImportError:
                            import xml.etree.ElementTree as ET
                        # Parse in chunks to avoid loading full XML into memory
                        try:
                            root = ET.fromstring(raw)
                            # Indeed RSS item structure: /rss/channel/item/title, description
                            for item in root.iter("item"):
                                title = (item.findtext("title") or "")[:500]
                                desc = (item.findtext("description") or "")[:2000]
                                link = (item.findtext("link") or "")[:500]
                                if title or desc:
                                    all_text_parts.append(f"{title} {desc}")
                                # Follow link for full description if available
                                if link and len(all_text_parts) < 50:
                                    try:
                                        async with session.get(link, timeout=aiohttp.ClientTimeout(total=10)) as lresp:
                                            if lresp.status == 200:
                                                page_text = await lresp.text()
                                                all_text_parts.append(page_text[:3000])
                                    except Exception:
                                        pass
                        except Exception:
                            # Malformed XML — try regex extraction as fallback
                            titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", raw)
                            descs = re.findall(r"<description><!\[CDATA\[(.*?)\]\]></description>", raw)
                            for t, d in zip(titles, descs, strict=False):
                                all_text_parts.append(f"{t} {d}"[:3000])
            except Exception:
                pass

        async def fetch_hn_jobs() -> None:
            """Fetch from Hacker News 'Who is Hiring' monthly threads via HN API."""
            try:
                session = await async_get_aiohttp_session()
                # Get topstories to find monthly "Who is Hiring" threads
                async with asyncio.timeout(15.0):
                    async with session.get(
                        "https://hacker-news.firebaseio.com/v0/topstories.json",
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status != 200:
                            return
                        story_ids = await resp.json()
                        # Scan top 100 stories for "who is hiring" threads
                        scanned = 0
                        for story_id in story_ids[:100]:
                            if scanned >= 20:
                                break
                            scanned += 1
                            async with session.get(
                                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                                timeout=aiohttp.ClientTimeout(total=5),
                            ) as item_resp:
                                if item_resp.status != 200:
                                    continue
                                item = await item_resp.json()
                                if not item:
                                    continue
                                title = item.get("title", "")
                                # Look for monthly "Who is Hiring" threads
                                if re.search(r"Who is Hiring\?.*\d{4}", title, re.IGNORECASE):
                                    # Get the text body of the thread
                                    if "text" in item and item["text"]:
                                        all_text_parts.append(item["text"][:5000])
                                    elif item.get("url"):
                                        # Fetch the actual hiring post URL
                                        async with session.get(
                                            item["url"],
                                            timeout=aiohttp.ClientTimeout(total=10),
                                        ) as text_resp:
                                            if text_resp.status == 200:
                                                all_text_parts.append(
                                                    (await text_resp.text())[:5000]
                                                )
            except Exception:
                pass

        async def fetch_remoteok_jobs() -> None:
            """Fetch from Remoteok.com API for remote job postings."""
            try:
                import urllib.parse

                encoded_tag = urllib.parse.quote_plus(entity_name)
                url = f"https://remoteok.io/api?tag={encoded_tag}"
                session = await async_get_aiohttp_session()
                async with asyncio.timeout(15.0):
                    async with session.get(
                        url,
                        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status != 200:
                            return
                        try:
                            import orjson
                            data = await resp.json(loads=orjson.loads)
                        except Exception:
                            import json
                            data = await resp.json(loads=json.loads)
                        # Remoteok API returns list of job objects
                        if not isinstance(data, list):
                            return
                        for job in data[:50]:  # cap at 50 postings
                            if not isinstance(job, dict):
                                continue
                            # Company name check — skip if not matching entity
                            company = job.get("company", "").lower()
                            if entity_name.lower() not in company and company not in entity_name.lower():
                                # Still include general tech jobs
                                pass
                            title = job.get("position", "") or job.get("title", "")
                            description = job.get("description", "")[:2000]
                            tags = job.get("tags", [])
                            if isinstance(tags, list):
                                description += " " + " ".join(str(t) for t in tags)
                            if title or description:
                                all_text_parts.append(f"{title} {description}"[:3000])
            except Exception:
                pass

        # Execute all three sources concurrently
        await asyncio.gather(
            fetch_indeed_jobs(),
            fetch_hn_jobs(),
            fetch_remoteok_jobs(),
            return_exceptions=True,
        )

        # Aggregate all text and extract tech signals
        combined_text = " ".join(all_text_parts[:200])  # cap at 200 postings worth of text

        # Extract tech using spaCy if available, regex fallback
        tech_counts = self._extract_tech_spacy(combined_text)

        # Extract seniority distribution
        seniority_totals = self._normalize_seniority(combined_text)

        # Extract hiring patterns
        for text_chunk in all_text_parts[:50]:
            for pat in self._extract_hiring_patterns(text_chunk):
                hiring_patterns_found.add(pat)
            for pp in self._extract_pain_points(text_chunk):
                pain_points_found.add(pp)

        return TechIntelligence(
            detected_technologies=tech_counts,
            hiring_patterns=sorted(hiring_patterns_found)[:10],
            seniority_distribution=seniority_totals,
            inferred_pain_points=sorted(pain_points_found)[:10],
        )

    async def cleanup(self) -> None:
        """Cleanup all system resources. Idempotent — safe to call multiple times."""
        try:
            # Signal aging task shutdown and wait for graceful exit
            if self._aging_shutdown.is_set():
                return  # already cleaned up — idempotent guard
            self._aging_shutdown.set()
            if self._aging_task:
                self._aging_task.cancel()
                try:
                    await self._aging_task
                except asyncio.CancelledError:
                    pass
                self._aging_task = None

            # Cancel active operations
            for operation_id in list(self.active_operations.keys()):
                operation = self.active_operations[operation_id]
                operation.status = OperationStatus.CANCELLED
                self._add_completed_operation(operation_id, operation)

            self.active_operations.clear()

            # F191C: cleanup must drain ALL transient structures
            self._completed_operations.clear()
            self._queued_ops.clear()
            self._queued_op_times.clear()
            while self.operation_queue:
                heapq.heappop(self.operation_queue)

            # Drain owned operation tasks — fail-soft, symmetric with _track_task
            for task in list(self._active_tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
            self._active_tasks.clear()

            # Cleanup components
            if self.intelligent_scraper:
                    await self.intelligent_scraper.close()

            if self.osint_aggregator:
                    await self.osint_aggregator.cleanup()

            if self.automation_orchestrator:
                    await self.automation_orchestrator.cleanup()

            logger.info("🔒 Unified Web Intelligence System cleanup completed")

        except Exception as e:
            logger.error(f"❌ Cleanup error: {e}")


# Factory function
async def create_unified_intelligence(config: dict[str, Any] | None = None) -> UnifiedWebIntelligence:
    """Factory function to create unified intelligence system."""
    system = UnifiedWebIntelligence(config)
    return system


# Example usage
async def example_usage():
    """Example usage of the unified intelligence system."""
    config = {
        'max_concurrent_operations': 3,
        'enable_flashattention': True,
        'enable_osint': True,
        'enable_stealth': True
    }

    intelligence_system = await create_unified_intelligence(config)

    # Create intelligence target
    target = IntelligenceTarget(
        target_id="target_001",
        name="Example Corporation",
        urls=["https://example.com", "https://example.com/careers"],
        selectors={
            'title': 'h1',
            'description': 'meta[name="description"]',
            'contact': '.contact-info'
        },
        osint_sources=["linkedin", "twitter", "whois"],
        operation_types=[
            IntelligenceOperationType.WEB_SCRAPING,
            IntelligenceOperationType.OSINT_COLLECTION,
            IntelligenceOperationType.THREAT_ASSESSMENT
        ],
        priority="high"
    )

    # Execute intelligence operation
    operation_id = await intelligence_system.execute_intelligence_operation(target)

    # Wait for completion and get results
    await asyncio.sleep(30)  # Wait for operation to complete

    status = await intelligence_system.get_operation_status(operation_id)
    print(f"Operation status: {status}")

    if status and status['status'] == 'completed':
        results = await intelligence_system.get_operation_results(operation_id)
        print(f"Results: {json.dumps(results, indent=2)}")

    # Get system metrics
    metrics = intelligence_system.get_system_metrics()
    print(f"System metrics: {json.dumps(metrics, indent=2)}")

    await intelligence_system.cleanup()


if __name__ == "__main__":
    import json
    asyncio.run(example_usage())
