"""
Universal Monitoring Coordinator
================================

Integrated monitoring coordination combining:
- DeepSeek R1: AdvancedMonitoring + Watchdog + psutil metrics
- Hermes3: Simplified initialization patterns
- M1 Master: Memory-aware monitoring with pressure detection

Unique Features Integrated:
1. Multi-source monitoring (AdvancedMonitoring, Watchdog, System metrics)
2. Background metrics collection (async task)
3. Performance benchmarking (CPU, Memory, General)
4. Historical metrics tracking (last 100 entries)
5. Health check orchestration
6. System resource monitoring (getrusage + mach host_statistics64 — no psutil in hot path)
7. Metrics aggregation and analysis
8. Alert generation on threshold breach
"""
import asyncio
import logging
import os
import resource
import time
from collections import deque
from dataclasses import dataclass
import msgspec
from enum import StrEnum
from typing import Any
from hledac.universal.core.system_metrics import get_system_snapshot
from hledac.universal.utils.async_helpers import safe_create_task
from .base import DecisionResponse, MemoryPressureLevel, OperationResult, OperationType, UniversalCoordinator
logger = logging.getLogger(__name__)

class _SecurityAuditorStub:
    """Minimal stub — prevents type-checker errors when SecurityAuditor is absent."""
    project_root: str | None
    __slots__ = tuple(('project_root',))

    def __init__(self, project_root: str | None=None, **_: Any) -> None:
        self.project_root = project_root

    async def audit_directory(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return {'findings': {}, 'total_issues': 0, 'critical_count': 0, 'high_risk_count': 0, 'security_score': 0, 'issues': [], 'recommendations': []}

class _SyntaxVerifierStub:
    """Minimal stub — prevents type-checker errors when SyntaxVerifier is absent."""
    __slots__ = tuple(('config',))

    def __init__(self, config: Any | None=None, **_: Any) -> None:
        self.config = config

    def verify_directory(self, path: str, **_: Any) -> Any:

        class _Result:
            all_valid: bool = True
            files_checked: list[str] = []
            valid_count: int = 0
            invalid_count: int = 0
            fixed_count: int = 0
            errors: list[Any] = []
        return _Result()

class _CodebaseIntegrityValidatorStub:
    """Minimal stub — prevents type-checker errors when validator is absent."""
    __slots__ = tuple(('config',))

    def __init__(self, config: Any | None=None, **_: Any) -> None:
        self.config = config

    def validate_directory(self, path: str, **_: Any) -> dict[str, Any]:
        return {'files_analyzed': 0, 'issues': [], 'integrity_score': 100, 'quality_grade': 'A', 'dummy_functions_count': 0, 'stub_files_count': 0}

class MetricType(StrEnum):
    """Types of metrics collected (StrEnum for direct JSON serialization)."""
    CPU = 'cpu'
    MEMORY = 'memory'
    DISK = 'disk'
    NETWORK = 'network'
    LOAD = 'load'
    TEMPERATURE = 'temperature'

class SystemMetrics(msgspec.Struct, gc=False):
    """System metrics snapshot."""
    timestamp: float
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    disk_percent: float
    network_connections: int
    load_average: tuple | None = None
    processes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {'timestamp': self.timestamp, 'cpu_percent': self.cpu_percent, 'memory_percent': self.memory_percent, 'memory_used_mb': self.memory_used_mb, 'memory_available_mb': self.memory_available_mb, 'disk_percent': self.disk_percent, 'network_connections': self.network_connections, 'load_average': self.load_average, 'processes': self.processes}

class MonitoringResult(msgspec.Struct, frozen=True, gc=False):
    """Result of monitoring operation."""
    monitoring_type: str
    success: bool
    summary: str
    metrics: dict[str, Any]
    execution_time: float
    alert_triggered: bool = False
    alert_message: str | None = None

class AlertThreshold(msgspec.Struct, frozen=True, gc=False):
    """Threshold configuration for alerts."""
    metric: str
    warning: float
    critical: float
    enabled: bool = True

class UniversalMonitoringCoordinator(UniversalCoordinator):
    """
    Universal coordinator for monitoring operations.

    Integrates three monitoring backends:
    1. AdvancedMonitoring - Advanced system monitoring
    2. Watchdog - Health check monitoring
    3. psutil - Direct system metrics collection

    Routing Strategy:
    - 'advanced'/'detailed' → AdvancedMonitoring
    - 'watchdog'/'health' → Watchdog
    - 'system'/'metrics' → System metrics (psutil)
    - 'performance'/'benchmark' → Performance benchmarking

    Background Collection:
    - Automatic metrics collection every 30 seconds
    - Maintains history of last 100 entries
    - Memory-aware (reduces frequency under pressure)
    """
    __slots__ = tuple(('_advanced_available', '_advanced_monitoring', '_alert_thresholds', '_alerts_enabled', '_alerts_triggered', '_benchmark_history', '_collection_interval', '_collection_task', '_collections_count', '_current_metrics', '_health_checks_performed', '_metrics_history', '_operation_stats', '_stop_collection', '_watchdog', '_watchdog_available'))

    def __init__(self, max_concurrent: int=10, collection_interval: float=30.0, max_history: int=100):
        super().__init__(name='universal_monitoring_coordinator', max_concurrent=max_concurrent, memory_aware=True)
        self._advanced_monitoring: Any | None = None
        self._watchdog: Any | None = None
        self._advanced_available = False
        self._watchdog_available = False
        self._collection_interval = collection_interval
        self._collection_task: asyncio.Task | None = None
        self._stop_collection = asyncio.Event()
        self._metrics_history: deque = deque(maxlen=max_history)
        self._current_metrics: SystemMetrics | None = None
        self._alert_thresholds: dict[str, AlertThreshold] = {'cpu_percent': AlertThreshold('cpu_percent', 70.0, 90.0), 'memory_percent': AlertThreshold('memory_percent', 75.0, 90.0), 'disk_percent': AlertThreshold('disk_percent', 80.0, 95.0)}
        self._alerts_enabled = True
        self._benchmark_history: deque = deque(maxlen=50)
        self._collections_count = 0
        self._alerts_triggered = 0
        self._health_checks_performed = 0
        self._operation_stats: dict[str, dict[str, Any]] = {}

    async def _do_initialize(self) -> bool:
        """Initialize monitoring subsystems with graceful degradation."""
        _AdvancedMonitoringImpl: type | None = None
        try:
            from hledac.monitoring.advanced_monitoring import AdvancedMonitoring as _AM
            _AdvancedMonitoringImpl = _AM
        except ImportError:
            logger.warning('MonitoringCoordinator: AdvancedMonitoring not available')
        except Exception as e:
            logger.warning(f'MonitoringCoordinator: AdvancedMonitoring init failed: {e}')
        if _AdvancedMonitoringImpl is not None:
            impl: Any = _AdvancedMonitoringImpl()
            if hasattr(impl, 'initialize'):
                await impl.initialize()
            self._advanced_monitoring = impl
            self._advanced_available = True
            logger.info('MonitoringCoordinator: AdvancedMonitoring initialized')
        _WatchdogImpl: type | None = None
        try:
            from utils.uma_budget import Watchdog as _WD
            _WatchdogImpl = _WD
        except ImportError:
            logger.warning('MonitoringCoordinator: Watchdog not available')
        except Exception as e:
            logger.warning(f'MonitoringCoordinator: Watchdog init failed: {e}')
        if _WatchdogImpl is not None:
            impl: Any = _WatchdogImpl()
            if hasattr(impl, 'start'):
                await impl.start()
            self._watchdog = impl
            self._watchdog_available = True
            logger.info('MonitoringCoordinator: Watchdog initialized')
        self._start_background_collection()
        return True

    async def _do_cleanup(self) -> None:
        """Cleanup monitoring subsystems."""
        self._stop_background_collection()
        if self._advanced_monitoring and hasattr(self._advanced_monitoring, 'cleanup'):
            try:
                await self._advanced_monitoring.cleanup()
            except Exception as e:
                logger.error(f'Error cleaning up AdvancedMonitoring: {e}')
        if self._watchdog and hasattr(self._watchdog, 'cleanup'):
            try:
                await self._watchdog.cleanup()
            except Exception as e:
                logger.error(f'Error cleaning up Watchdog: {e}')
        self._metrics_history.clear()
        self._benchmark_history.clear()

    def _start_background_collection(self) -> None:
        """Start background metrics collection task."""
        if self._collection_task is None or self._collection_task.done():
            self._stop_collection.clear()
            self._collection_task = safe_create_task(self._background_collection_loop(), name='monitoring:background_collection')
            logger.info('MonitoringCoordinator: Background collection started')

    def _stop_background_collection(self) -> None:
        """Stop background metrics collection."""
        if self._collection_task and (not self._collection_task.done()):
            self._stop_collection.set()

    def get_supported_operations(self) -> list[OperationType]:
        """Return supported operation types."""
        return [OperationType.MONITORING]

    async def handle_request(self, operation_ref: str, decision: DecisionResponse) -> OperationResult:
        """
        Handle monitoring request with intelligent routing.

        Args:
            operation_ref: Unique operation reference
            decision: Monitoring decision with routing info

        Returns:
            OperationResult with monitoring outcome
        """
        start_time = time.time()
        operation_id = self.generate_operation_id()
        try:
            self.track_operation(operation_id, {'operation_ref': operation_ref, 'decision': decision, 'type': 'monitoring'})
            result = await self._execute_monitoring_decision(decision)
            operation_result = OperationResult(operation_id=operation_id, status='completed' if result.success else 'failed', result_summary=result.summary, execution_time=time.time() - start_time, success=result.success, metadata={'monitoring_type': result.monitoring_type, 'alert_triggered': result.alert_triggered, 'metrics_collected': len(result.metrics)})
        except Exception as e:
            operation_result = OperationResult(operation_id=operation_id, status='failed', result_summary=f'Monitoring failed: {str(e)}', execution_time=time.time() - start_time, success=False, error_message=str(e))
        finally:
            self.untrack_operation(operation_id)
        self.record_operation_result(operation_result)
        return operation_result

    async def _execute_monitoring_decision(self, decision: DecisionResponse) -> MonitoringResult:
        """Route monitoring decision to appropriate backend."""
        chosen = decision.chosen_option.lower()
        if 'advanced' in chosen or 'detailed' in chosen:
            if self._advanced_available:
                return await self._execute_advanced_monitoring(decision)
        elif 'watchdog' in chosen or 'health' in chosen:
            if self._watchdog_available:
                return await self._execute_watchdog_monitoring(decision)
        elif 'performance' in chosen or 'benchmark' in chosen:
            return await self._execute_performance_monitoring(decision)
        return await self._execute_system_monitoring()

    async def _execute_advanced_monitoring(self, decision: DecisionResponse) -> MonitoringResult:
        """Execute advanced monitoring."""
        start_time = time.time()
        if not self._advanced_monitoring:
            raise RuntimeError('AdvancedMonitoring not available')
        monitoring_result = await self._advanced_monitoring.perform_monitoring(monitoring_type=decision.chosen_option, context=decision.reasoning, priority=decision.confidence)
        execution_time = time.time() - start_time
        return MonitoringResult(monitoring_type='advanced', success=monitoring_result.get('success', False), summary=f"Advanced monitoring: {monitoring_result.get('metrics_collected', 0)} metrics", metrics=monitoring_result, execution_time=execution_time)

    async def _execute_watchdog_monitoring(self, decision: DecisionResponse) -> MonitoringResult:
        """Execute watchdog health monitoring."""
        start_time = time.time()
        if not self._watchdog:
            raise RuntimeError('Watchdog not available')
        health_result = await self._watchdog.perform_health_check(check_type=decision.chosen_option, detailed=decision.confidence > 0.7)
        execution_time = time.time() - start_time
        self._health_checks_performed += 1
        return MonitoringResult(monitoring_type='watchdog', success=health_result.get('healthy', False), summary=f"Health check: {health_result.get('status', 'unknown')} status", metrics=health_result, execution_time=execution_time)

    async def _execute_system_monitoring(self) -> MonitoringResult:
        """Execute system-level monitoring via cached mach/getrusage (no psutil syscalls in hot path).

        E3 FIX: Replaces raw psutil calls with get_system_snapshot():
          - getrusage(RUSAGE_SELF) for RSS — ZERO syscall after first call
          - mach host_statistics64 for memory pressure — ~50µs warm, cached 200ms
          - cpu_percent(interval=1) REMOVED — was blocking for 1 second per call
          - disk/net/pids kept as-is (only called in 30s background loop)
        """
        start_time = time.time()
        try:
            snap = get_system_snapshot()
            import psutil as _ps
            disk = _ps.disk_usage('/')
            metrics = SystemMetrics(timestamp=time.time(), cpu_percent=0.0, memory_percent=snap.memory_percent, memory_used_mb=snap.rss_mb, memory_available_mb=snap.memory_available_gb * 1024, disk_percent=disk.percent, network_connections=len(_ps.net_connections()), load_average=snap.load_average, processes=len(_ps.pids()))
            self._current_metrics = metrics
            self._metrics_history.append(metrics)
            self._collections_count += 1
            alert_triggered, alert_message = self._check_alerts(metrics)
            if alert_triggered:
                self._alerts_triggered += 1
            execution_time = time.time() - start_time
            return MonitoringResult(monitoring_type='system', success=True, summary=f'System: Memory {metrics.memory_percent:.1f}%', metrics=metrics.to_dict(), execution_time=execution_time, alert_triggered=alert_triggered, alert_message=alert_message)
        except Exception as e:
            return MonitoringResult(monitoring_type='system', success=False, summary=f'System monitoring failed: {str(e)}', metrics={}, execution_time=time.time() - start_time)

    async def _execute_performance_monitoring(self, decision: DecisionResponse) -> MonitoringResult:
        """Execute performance benchmarking."""
        start_time = time.time()
        benchmark_type = decision.metadata.get('benchmark_type', 'general')
        duration = min(decision.estimated_duration, 60)
        result = await self._run_performance_benchmark(benchmark_type, int(duration))
        execution_time = time.time() - start_time
        return MonitoringResult(monitoring_type='performance', success=True, summary=f"Benchmark: {result.get('operations_per_second', 0):.0f} ops/sec", metrics=result, execution_time=execution_time)

    async def _run_performance_benchmark(self, benchmark_type: str, duration: int) -> dict[str, Any]:
        """Run a performance benchmark."""
        start_time = time.time()
        operations = 0
        if benchmark_type.lower().startswith('cpu'):
            while time.time() - start_time < duration:
                _ = sum((i * i for i in range(1000)))
                operations += 1
        elif benchmark_type.lower().startswith('memory'):
            data = deque()
            while time.time() - start_time < duration:
                data.append(list(range(1000)))
                if len(data) > 100:
                    data.popleft()
                operations += 1
        else:
            while time.time() - start_time < duration:
                operations += 1
        elapsed = time.time() - start_time
        result = {'benchmark_type': benchmark_type, 'duration': elapsed, 'operations': operations, 'operations_per_second': operations / elapsed if elapsed > 0 else 0, 'start_time': start_time, 'end_time': time.time()}
        self._benchmark_history.append(result)
        return result

    async def _background_collection_loop(self) -> None:
        """Background task to collect system metrics."""
        while not self._stop_collection.is_set():
            try:
                await self._execute_system_monitoring()
                interval = self._collection_interval
                if self._current_memory_pressure == MemoryPressureLevel.ELEVATED:
                    interval *= 1.5
                elif self._current_memory_pressure == MemoryPressureLevel.HIGH:
                    interval *= 2.0
                elif self._current_memory_pressure == MemoryPressureLevel.CRITICAL:
                    interval *= 3.0
                try:
                    async with asyncio.timeout(interval):
                        await self._stop_collection.wait()
                except TimeoutError:
                    pass
            except Exception as e:
                logger.error(f'Background collection error: {e}')
                await asyncio.sleep(self._collection_interval)

    def _check_alerts(self, metrics: SystemMetrics) -> tuple[bool, str | None]:
        """Check if any alert thresholds are breached."""
        if not self._alerts_enabled:
            return (False, None)
        alerts = []
        cpu_threshold = self._alert_thresholds.get('cpu_percent')
        if cpu_threshold and cpu_threshold.enabled:
            if metrics.cpu_percent >= cpu_threshold.critical:
                alerts.append(f'CRITICAL: CPU {metrics.cpu_percent:.1f}%')
            elif metrics.cpu_percent >= cpu_threshold.warning:
                alerts.append(f'WARNING: CPU {metrics.cpu_percent:.1f}%')
        memory_threshold = self._alert_thresholds.get('memory_percent')
        if memory_threshold and memory_threshold.enabled:
            if metrics.memory_percent >= memory_threshold.critical:
                alerts.append(f'CRITICAL: Memory {metrics.memory_percent:.1f}%')
            elif metrics.memory_percent >= memory_threshold.warning:
                alerts.append(f'WARNING: Memory {metrics.memory_percent:.1f}%')
        disk_threshold = self._alert_thresholds.get('disk_percent')
        if disk_threshold and disk_threshold.enabled:
            if metrics.disk_percent >= disk_threshold.critical:
                alerts.append(f'CRITICAL: Disk {metrics.disk_percent:.1f}%')
            elif metrics.disk_percent >= disk_threshold.warning:
                alerts.append(f'WARNING: Disk {metrics.disk_percent:.1f}%')
        if alerts:
            return (True, ' | '.join(alerts))
        return (False, None)

    def set_alert_threshold(self, metric: str, warning: float, critical: float, enabled: bool=True) -> None:
        """Set alert threshold for a metric."""
        self._alert_thresholds[metric] = AlertThreshold(metric=metric, warning=warning, critical=critical, enabled=enabled)

    def enable_alerts(self, enabled: bool=True) -> None:
        """Enable or disable alerts."""
        self._alerts_enabled = enabled

    def get_current_metrics(self) -> SystemMetrics | None:
        """Get current system metrics."""
        return self._current_metrics

    def get_metrics_history(self, limit: int=10, metric_type: str | None=None) -> list[dict[str, Any]]:
        """Get historical system metrics."""
        entries = list(self._metrics_history)[-limit:]
        return [m.to_dict() for m in entries]

    def get_average_metrics(self, last_n: int=10) -> dict[str, float]:
        """Get average metrics over last N samples."""
        entries = list(self._metrics_history)[-last_n:]
        if not entries:
            return {}
        return {'avg_cpu_percent': sum((m.cpu_percent for m in entries)) / len(entries), 'avg_memory_percent': sum((m.memory_percent for m in entries)) / len(entries), 'avg_disk_percent': sum((m.disk_percent for m in entries)) / len(entries), 'avg_network_connections': sum((m.network_connections for m in entries)) / len(entries)}

    def get_peak_metrics(self, last_n: int=10) -> dict[str, float]:
        """Get peak metrics over last N samples."""
        entries = list(self._metrics_history)[-last_n:]
        if not entries:
            return {}
        return {'peak_cpu_percent': max((m.cpu_percent for m in entries)), 'peak_memory_percent': max((m.memory_percent for m in entries)), 'peak_disk_percent': max((m.disk_percent for m in entries)), 'peak_network_connections': max((m.network_connections for m in entries))}

    def get_benchmark_history(self, limit: int=10) -> list[dict[str, Any]]:
        """Get recent benchmark results."""
        return list(self._benchmark_history)[-limit:]

    def get_average_benchmark(self, benchmark_type: str) -> dict[str, Any] | None:
        """Get average benchmark results for a specific type."""
        entries = [b for b in self._benchmark_history if b.get('benchmark_type') == benchmark_type]
        if not entries:
            return None
        return {'benchmark_type': benchmark_type, 'avg_operations_per_second': sum((b.get('operations_per_second', 0) for b in entries)) / len(entries), 'total_runs': len(entries)}

    async def perform_health_check(self, detailed: bool=False) -> dict[str, Any]:
        """Perform comprehensive health check."""
        health = {'status': 'healthy', 'timestamp': time.time(), 'checks': {}}
        if self._current_metrics:
            metrics = self._current_metrics
            health['checks']['resources'] = {'cpu_ok': metrics.cpu_percent < 90, 'memory_ok': metrics.memory_percent < 90, 'disk_ok': metrics.disk_percent < 95, 'cpu_percent': metrics.cpu_percent, 'memory_percent': metrics.memory_percent, 'disk_percent': metrics.disk_percent}
        health['checks']['subsystems'] = {'advanced_monitoring': self._advanced_available, 'watchdog': self._watchdog_available, 'background_collection': self._collection_task is not None and (not self._collection_task.done())}
        if detailed:
            health['metrics_summary'] = self.get_average_metrics(5)
            health['peak_metrics'] = self.get_peak_metrics(5)
            health['collection_stats'] = {'total_collections': self._collections_count, 'alerts_triggered': self._alerts_triggered, 'health_checks': self._health_checks_performed}
        resource_checks = health['checks'].get('resources', {})
        if not all([resource_checks.get('cpu_ok', True), resource_checks.get('memory_ok', True), resource_checks.get('disk_ok', True)]):
            health['status'] = 'degraded'
        return health

    async def run_security_audit(self, target_path: str | None=None, include_patterns: list[str] | None=None, exclude_patterns: list[str] | None=None) -> dict[str, Any]:
        """
        Run OWASP security audit on codebase.

        Integrated from: tools/audit/security_auditor.py

        Detects:
        - SQL injection vulnerabilities
        - XSS vulnerabilities
        - Path traversal issues
        - Hardcoded secrets (API keys, passwords)
        - Weak cryptographic algorithms

        Args:
            target_path: Path to audit (default: project root)
            include_patterns: File patterns to include
            exclude_patterns: File patterns to exclude

        Returns:
            Security audit report
        """
        try:
            from hledac.tools.audit.security_auditor import SecurityAuditor
            auditor = SecurityAuditor()
            target = target_path or os.getcwd()
            report = await auditor.audit_directory(path=target, include_patterns=include_patterns or ['*.py', '*.js', '*.ts'], exclude_patterns=exclude_patterns or ['**/node_modules/**', '**/.venv/**', '**/__pycache__/**', '**/dist/**', '**/build/**', '**/tests/**'])
            return {'success': True, 'target': target, 'files_scanned': len(report.get('findings', {})), 'issues_found': report.get('total_issues', 0), 'critical_issues': report.get('critical_count', 0), 'high_risk_issues': report.get('high_risk_count', 0), 'security_score': report.get('security_score', 0), 'issues': report.get('issues', []), 'recommendations': report.get('recommendations', [])}
        except ImportError:
            logger.warning('SecurityAuditor module not available')
            return {'success': False, 'error': 'SecurityAuditor not available'}
        except Exception as e:
            logger.error(f'Security audit failed: {e}')
            return {'success': False, 'error': str(e)}

    async def verify_codebase_integrity(self, target_path: str | None=None, min_lines_of_code: int=5, strict_mode: bool=False) -> dict[str, Any]:
        """
        Validate codebase integrity and detect low-quality code.

        Integrated from: tools/diagnostics/codebase_integrity_validator.py

        Detects:
        - Dummy/stub functions (pass, return None, NotImplementedError)
        - Empty or near-empty files
        - TODO/FIXME comments
        - High complexity without implementation
        - Unused imports

        Args:
            target_path: Path to validate (default: project root)
            min_lines_of_code: Minimum expected LOC for implementation files
            strict_mode: Fail on TODO comments and warnings

        Returns:
            Integrity validation report
        """
        try:
            from hledac.tools.diagnostics.codebase_integrity_validator import CodebaseIntegrityValidator, ValidationConfig
            config = ValidationConfig(min_lines_of_code=min_lines_of_code, strict_mode=strict_mode)
            validator = CodebaseIntegrityValidator(config)
            target = target_path or os.getcwd()
            result = validator.validate_directory(target)
            return {'success': True, 'target': target, 'files_analyzed': result['files_analyzed'], 'issues_found': len(result.get('issues', [])), 'integrity_score': result['integrity_score'], 'quality_grade': result['quality_grade'], 'dummy_functions': result.get('dummy_functions_count', 0), 'stub_files': result.get('stub_files_count', 0), 'issues': result.get('issues', [])[:20], 'recommendations': result.get('recommendations', []), 'passed': result['integrity_score'] >= 80}
        except ImportError:
            logger.warning('CodebaseIntegrityValidator not available')
            return {'success': False, 'error': 'Validator not available'}
        except Exception as e:
            logger.error(f'Codebase integrity check failed: {e}')
            return {'success': False, 'error': str(e)}

    async def verify_syntax_batch(self, target_path: str | None=None, auto_fix: bool=True, parallel: bool=True) -> dict[str, Any]:
        """
        Verify Python syntax across codebase with optional auto-fix.

        Integrated from: tools/audit/syntax_verifier.py

        Args:
            target_path: Path to verify (default: project root)
            auto_fix: Automatically fix common issues
            parallel: Use parallel processing

        Returns:
            Syntax verification report
        """
        try:
            from hledac.tools.audit.syntax_verifier import SyntaxVerifier, VerificationConfig
            config = VerificationConfig(auto_fix=auto_fix, parallel=parallel, max_workers=4)
            verifier = SyntaxVerifier(config)
            target = target_path or os.getcwd()
            result = verifier.verify_directory(target)
            return {'success': result.all_valid, 'target': target, 'files_checked': len(result.files_checked), 'valid_files': result.valid_count, 'invalid_files': result.invalid_count, 'fixed_files': result.fixed_count, 'errors': [{'file': e.file, 'line': e.line, 'message': e.message} for e in result.errors[:10]], 'all_valid': result.all_valid}
        except ImportError:
            logger.warning('SyntaxVerifier not available')
            return {'success': False, 'error': 'SyntaxVerifier not available'}
        except Exception as e:
            logger.error(f'Syntax verification failed: {e}')
            return {'success': False, 'error': str(e)}

    def _get_feature_list(self) -> list[str]:
        """Report available features."""
        features = ['System metrics collection (psutil)', 'Background metrics collection', 'Historical metrics tracking', 'Alert threshold management', 'Performance benchmarking', 'OWASP security auditing', 'Codebase integrity validation', 'Batch syntax verification']
        if self._advanced_available:
            features.append('Advanced system monitoring')
        if self._watchdog_available:
            features.append('Health check monitoring')
        features.extend(['Metrics aggregation and analysis', 'Peak/average metrics calculation', 'Comprehensive health checks', 'Security vulnerability scanning', 'Dummy/stub code detection'])
        return features

    def get_monitoring_stats(self) -> dict[str, Any]:
        """Get monitoring statistics."""
        return {'collections_count': self._collections_count, 'alerts_triggered': self._alerts_triggered, 'health_checks_performed': self._health_checks_performed, 'metrics_history_size': len(self._metrics_history), 'benchmark_history_size': len(self._benchmark_history), 'background_collection_active': self._collection_task is not None and (not self._collection_task.done()), 'current_memory_pressure': self._current_memory_pressure.value}

    def get_available_monitoring_systems(self) -> dict[str, bool]:
        """Get availability status of all monitoring systems."""
        return {'advanced_monitoring': self._advanced_available, 'watchdog': self._watchdog_available, 'system_metrics': True, 'background_collection': self._collection_task is not None and (not self._collection_task.done())}

    def track_operation_metrics(self, operation_type: str, success: bool, duration: float, metadata: dict[str, Any] | None=None) -> None:
        """
        Track operation with statistics (from Hermes3).

        Args:
            operation_type: Type of operation
            success: Whether operation succeeded
            duration: Execution duration in seconds
            metadata: Optional metadata
        """
        if operation_type not in self._operation_stats:
            self._operation_stats[operation_type] = {'total': 0, 'successful': 0, 'failed': 0, 'avg_duration': 0.0, 'total_duration': 0.0, 'min_duration': float('inf'), 'max_duration': 0.0, 'last_executed': None}
        stats = self._operation_stats[operation_type]
        stats['total'] += 1
        stats['successful'] += 1 if success else 0
        stats['failed'] += 0 if success else 1
        stats['total_duration'] += duration
        stats['avg_duration'] = stats['total_duration'] / stats['total']
        stats['min_duration'] = min(stats['min_duration'], duration)
        stats['max_duration'] = max(stats['max_duration'], duration)
        stats['last_executed'] = time.time()

    def get_operation_stats(self, operation_type: str | None=None) -> dict[str, Any]:
        """
        Get operation statistics (from Hermes3).

        Args:
            operation_type: Specific operation type (None = all)

        Returns:
            Operation statistics
        """
        if operation_type:
            return self._operation_stats.get(operation_type, {})
        return self._operation_stats.copy()

    def get_health_status(self) -> dict[str, Any]:
        """
        Get health status (from Hermes3).

        Returns:
            Health status with metrics
        """
        latest = self._current_metrics
        if latest is None:
            return {'status': 'unknown', 'reason': 'No metrics collected yet'}
        memory_percent = latest.memory_percent
        if memory_percent > 90:
            health = 'critical'
            reason = f'Memory usage critical: {memory_percent:.1f}%'
        elif memory_percent > 75:
            health = 'warning'
            reason = f'Memory usage high: {memory_percent:.1f}%'
        elif memory_percent > 60:
            health = 'elevated'
            reason = f'Memory usage elevated: {memory_percent:.1f}%'
        else:
            health = 'healthy'
            reason = f'Memory usage normal: {memory_percent:.1f}%'
        return {'status': health, 'reason': reason, 'cpu_percent': latest.cpu_percent, 'memory_percent': latest.memory_percent, 'memory_mb': latest.memory_used_mb, 'collection_count': self._collections_count, 'alerts_triggered': self._alerts_triggered}

    async def run_diagnostics(self, component: str | None=None, auto_fix: bool=False) -> dict[str, Any]:
        """
        Run automated diagnostics and troubleshooting.

        Integrated from: tools/preserved_logic/monitoring/diagnostics_engine.py

        Features:
        - Automated system diagnostics
        - Component-specific health checks
        - Issue detection and recommendations
        - Auto-fix capabilities (optional)

        Args:
            component: Specific component to diagnose (None = all)
            auto_fix: Automatically apply fixes if available

        Returns:
            Diagnostics report with issues and recommendations
        """
        try:
            from hledac.tools.preserved_logic.monitoring.diagnostics_engine import DiagnosticResult, DiagnosticsEngine
            engine = DiagnosticsEngine(enable_auto_diagnostics=False, m1_optimization=True)
            issues = []
            if component:
                component_issues = await engine.run_manual_diagnostic(component)
                issues.extend(component_issues)
            else:
                components = ['memory', 'system', 'network', 'storage']
                from hledac.universal.utils.async_helpers import chunked_taskgroup
                results = await chunked_taskgroup(components, engine.run_manual_diagnostic, batch_size=20, concurrency=20, ctx='monitoring.diagnostics')
                for comp_issues in results:
                    issues.extend(comp_issues)
            issues_dict = [{'issue_id': issue.issue_id, 'component': issue.component, 'severity': issue.severity.value, 'description': issue.description, 'recommendations': issue.recommendations, 'resolved': issue.resolved, 'timestamp': issue.timestamp} for issue in issues]
            critical_count = sum((1 for i in issues if i.severity.value in ['critical', 'error']))
            return {'success': True, 'component': component or 'all', 'issues_found': len(issues), 'critical_issues': critical_count, 'issues': issues_dict, 'auto_fix_enabled': auto_fix, 'recommendations': [rec for issue in issues for rec in issue.recommendations]}
        except ImportError:
            logger.warning('DiagnosticsEngine not available')
            return {'success': False, 'error': 'DiagnosticsEngine not available', 'component': component}
        except Exception as e:
            logger.error(f'Diagnostics failed: {e}')
            return {'success': False, 'error': str(e), 'component': component}

    async def start_auto_diagnostics(self, interval_seconds: int=60, enable_auto_fix: bool=False) -> dict[str, Any]:
        """
        Start automated diagnostics monitoring.

        Args:
            interval_seconds: Diagnostic check interval
            enable_auto_fix: Enable automatic issue fixing

        Returns:
            Start confirmation
        """
        try:
            from hledac.tools.preserved_logic.monitoring.diagnostics_engine import DiagnosticsEngine
            if not hasattr(self, '_diagnostics_engine'):
                self._diagnostics_engine = DiagnosticsEngine(enable_auto_diagnostics=True, diagnostic_interval=interval_seconds, m1_optimization=True)
            success = await self._diagnostics_engine.start_diagnostics()
            return {'success': success, 'interval_seconds': interval_seconds, 'auto_fix_enabled': enable_auto_fix, 'message': 'Auto-diagnostics started' if success else 'Already running'}
        except Exception as e:
            logger.error(f'Failed to start auto-diagnostics: {e}')
            return {'success': False, 'error': str(e)}

    async def stop_auto_diagnostics(self) -> dict[str, Any]:
        """Stop automated diagnostics monitoring."""
        if hasattr(self, '_diagnostics_engine'):
            success = await self._diagnostics_engine.stop_diagnostics()
            return {'success': success, 'message': 'Auto-diagnostics stopped'}
        return {'success': False, 'message': 'Not running'}