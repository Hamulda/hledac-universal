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
6. System resource monitoring via psutil
7. Metrics aggregation and analysis
8. Alert generation on threshold breach
"""

from __future__ import annotations

import time
import asyncio
import psutil
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
from collections import deque
from enum import Enum
import logging

from .base import (
    UniversalCoordinator,
    OperationType,
    DecisionResponse,
    OperationResult,
    MemoryPressureLevel
)

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """Types of metrics collected."""
    CPU = "cpu"
    MEMORY = "memory"
    DISK = "disk"
    NETWORK = "network"
    LOAD = "load"
    TEMPERATURE = "temperature"


@dataclass
class SystemMetrics:
    """System metrics snapshot."""
    timestamp: float
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    disk_percent: float
    network_connections: int
    load_average: Optional[tuple] = None
    processes: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'timestamp': self.timestamp,
            'cpu_percent': self.cpu_percent,
            'memory_percent': self.memory_percent,
            'memory_used_mb': self.memory_used_mb,
            'memory_available_mb': self.memory_available_mb,
            'disk_percent': self.disk_percent,
            'network_connections': self.network_connections,
            'load_average': self.load_average,
            'processes': self.processes
        }


@dataclass
class MonitoringResult:
    """Result of monitoring operation."""
    monitoring_type: str  # 'advanced', 'watchdog', 'system', 'performance'
    success: bool
    summary: str
    metrics: Dict[str, Any]
    execution_time: float
    alert_triggered: bool = False
    alert_message: Optional[str] = None


@dataclass
class AlertThreshold:
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

    def __init__(
        self,
        max_concurrent: int = 10,
        collection_interval: float = 30.0,
        max_history: int = 100
    ):
        super().__init__(
            name="universal_monitoring_coordinator",
            max_concurrent=max_concurrent,
            memory_aware=True
        )
        
        # Monitoring subsystems
        self._advanced_monitoring: Optional[Any] = None
        self._watchdog: Optional[Any] = None
        
        # Availability flags
        self._advanced_available = False
        self._watchdog_available = False
        
        # Background collection
        self._collection_interval = collection_interval
        self._collection_task: Optional[asyncio.Task] = None
        self._stop_collection = asyncio.Event()
        
        # Metrics storage
        self._metrics_history: deque = deque(maxlen=max_history)
        self._current_metrics: Optional[SystemMetrics] = None
        
        # Alert configuration
        self._alert_thresholds: Dict[str, AlertThreshold] = {
            'cpu_percent': AlertThreshold('cpu_percent', 70.0, 90.0),
            'memory_percent': AlertThreshold('memory_percent', 75.0, 90.0),
            'disk_percent': AlertThreshold('disk_percent', 80.0, 95.0),
        }
        self._alerts_enabled = True
        
        # Benchmark tracking
        self._benchmark_history: deque = deque(maxlen=50)
        
        # Monitoring stats
        self._collections_count = 0
        self._alerts_triggered = 0
        self._health_checks_performed = 0
        
        # Hermes3: Operation statistics
        self._operation_stats: Dict[str, Dict[str, Any]] = {}

    # ========================================================================
    # Initialization
    # ========================================================================

    async def _do_initialize(self) -> bool:
        """Initialize monitoring subsystems with graceful degradation."""
        initialized_any = False
        
        # Try AdvancedMonitoring
        try:
            from hledac.monitoring.advanced_monitoring import AdvancedMonitoring
            self._advanced_monitoring = AdvancedMonitoring()
            if hasattr(self._advanced_monitoring, 'initialize'):
                await self._advanced_monitoring.initialize()
            self._advanced_available = True
            initialized_any = True
            logger.info("MonitoringCoordinator: AdvancedMonitoring initialized")
        except ImportError:
            logger.warning("MonitoringCoordinator: AdvancedMonitoring not available")
        except Exception as e:
            logger.warning(f"MonitoringCoordinator: AdvancedMonitoring init failed: {e}")
        
        # Try Watchdog
        try:
            from hledac.core.watchdog import Watchdog
            self._watchdog = Watchdog()
            if hasattr(self._watchdog, 'start'):
                await self._watchdog.start()
            self._watchdog_available = True
            initialized_any = True
            logger.info("MonitoringCoordinator: Watchdog initialized")
        except ImportError:
            logger.warning("MonitoringCoordinator: Watchdog not available")
        except Exception as e:
            logger.warning(f"MonitoringCoordinator: Watchdog init failed: {e}")
        
        # Always have psutil-based monitoring
        initialized_any = True
        
        # Start background collection
        self._start_background_collection()
        
        return initialized_any

    async def _do_cleanup(self) -> None:
        """Cleanup monitoring subsystems."""
        # Stop background collection
        self._stop_background_collection()
        
        if self._advanced_monitoring and hasattr(self._advanced_monitoring, 'cleanup'):
            try:
                await self._advanced_monitoring.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up AdvancedMonitoring: {e}")
        
        if self._watchdog and hasattr(self._watchdog, 'cleanup'):
            try:
                await self._watchdog.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up Watchdog: {e}")
        
        self._metrics_history.clear()
        self._benchmark_history.clear()

    def _start_background_collection(self) -> None:
        """Start background metrics collection task."""
        if self._collection_task is None or self._collection_task.done():
            self._stop_collection.clear()
            self._collection_task = asyncio.create_task(
                self._background_collection_loop()
            )
            logger.info("MonitoringCoordinator: Background collection started")

    def _stop_background_collection(self) -> None:
        """Stop background metrics collection."""
        if self._collection_task and not self._collection_task.done():
            self._stop_collection.set()
            # Don't await here to avoid blocking cleanup

    # ========================================================================
    # Core Operations
    # ========================================================================

    def get_supported_operations(self) -> List[OperationType]:
        """Return supported operation types."""
        return [OperationType.MONITORING]

    async def handle_request(
        self,
        operation_ref: str,
        decision: DecisionResponse
    ) -> OperationResult:
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
            # Track operation
            self.track_operation(operation_id, {
                'operation_ref': operation_ref,
                'decision': decision,
                'type': 'monitoring'
            })
            
            # Route to appropriate monitoring method
            result = await self._execute_monitoring_decision(decision)
            
            # Create operation result
            operation_result = OperationResult(
                operation_id=operation_id,
                status="completed" if result.success else "failed",
                result_summary=result.summary,
                execution_time=time.time() - start_time,
                success=result.success,
                metadata={
                    'monitoring_type': result.monitoring_type,
                    'alert_triggered': result.alert_triggered,
                    'metrics_collected': len(result.metrics),
                }
            )
            
        except Exception as e:
            operation_result = OperationResult(
                operation_id=operation_id,
                status="failed",
                result_summary=f"Monitoring failed: {str(e)}",
                execution_time=time.time() - start_time,
                success=False,
                error_message=str(e)
            )
        finally:
            self.untrack_operation(operation_id)
        
        # Record metrics
        self.record_operation_result(operation_result)
        return operation_result

    # ========================================================================
    # Monitoring Routing and Execution
    # ========================================================================

    async def _execute_monitoring_decision(
        self,
        decision: DecisionResponse
    ) -> MonitoringResult:
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
        
        # Default: System monitoring
        return await self._execute_system_monitoring()

    async def _execute_advanced_monitoring(
        self,
        decision: DecisionResponse
    ) -> MonitoringResult:
        """Execute advanced monitoring."""
        start_time = time.time()
        
        if not self._advanced_monitoring:
            raise RuntimeError("AdvancedMonitoring not available")
        
        monitoring_result = await self._advanced_monitoring.perform_monitoring(
            monitoring_type=decision.chosen_option,
            context=decision.reasoning,
            priority=decision.confidence
        )
        
        execution_time = time.time() - start_time
        
        return MonitoringResult(
            monitoring_type='advanced',
            success=monitoring_result.get('success', False),
            summary=f"Advanced monitoring: {monitoring_result.get('metrics_collected', 0)} metrics",
            metrics=monitoring_result,
            execution_time=execution_time
        )

    async def _execute_watchdog_monitoring(
        self,
        decision: DecisionResponse
    ) -> MonitoringResult:
        """Execute watchdog health monitoring."""
        start_time = time.time()
        
        if not self._watchdog:
            raise RuntimeError("Watchdog not available")
        
        health_result = await self._watchdog.perform_health_check(
            check_type=decision.chosen_option,
            detailed=decision.confidence > 0.7
        )
        
        execution_time = time.time() - start_time
        self._health_checks_performed += 1
        
        return MonitoringResult(
            monitoring_type='watchdog',
            success=health_result.get('healthy', False),
            summary=f"Health check: {health_result.get('status', 'unknown')} status",
            metrics=health_result,
            execution_time=execution_time
        )

    async def _execute_system_monitoring(self) -> MonitoringResult:
        """Execute system-level monitoring via psutil."""
        start_time = time.time()
        
        try:
            # Collect system metrics
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            metrics = SystemMetrics(
                timestamp=time.time(),
                cpu_percent=psutil.cpu_percent(interval=1),
                memory_percent=memory.percent,
                memory_used_mb=memory.used / (1024 * 1024),
                memory_available_mb=memory.available / (1024 * 1024),
                disk_percent=disk.percent,
                network_connections=len(psutil.net_connections()),
                load_average=psutil.getloadavg() if hasattr(psutil, 'getloadavg') else None,
                processes=len(psutil.pids())
            )
            
            # Store metrics
            self._current_metrics = metrics
            self._metrics_history.append(metrics)
            self._collections_count += 1
            
            # Check for alerts
            alert_triggered, alert_message = self._check_alerts(metrics)
            if alert_triggered:
                self._alerts_triggered += 1
            
            execution_time = time.time() - start_time
            
            return MonitoringResult(
                monitoring_type='system',
                success=True,
                summary=f"System: CPU {metrics.cpu_percent:.1f}%, Memory {metrics.memory_percent:.1f}%",
                metrics=metrics.to_dict(),
                execution_time=execution_time,
                alert_triggered=alert_triggered,
                alert_message=alert_message
            )
            
        except Exception as e:
            return MonitoringResult(
                monitoring_type='system',
                success=False,
                summary=f"System monitoring failed: {str(e)}",
                metrics={},
                execution_time=time.time() - start_time
            )

    async def _execute_performance_monitoring(
        self,
        decision: DecisionResponse
    ) -> MonitoringResult:
        """Execute performance benchmarking."""
        start_time = time.time()
        
        benchmark_type = decision.metadata.get('benchmark_type', 'general')
        duration = min(decision.estimated_duration, 60)  # Max 60 seconds
        
        result = await self._run_performance_benchmark(benchmark_type, duration)
        
        execution_time = time.time() - start_time
        
        return MonitoringResult(
            monitoring_type='performance',
            success=True,
            summary=f"Benchmark: {result.get('operations_per_second', 0):.0f} ops/sec",
            metrics=result,
            execution_time=execution_time
        )

    async def _run_performance_benchmark(
        self,
        benchmark_type: str,
        duration: int
    ) -> Dict[str, Any]:
        """Run a performance benchmark."""
        start_time = time.time()
        operations = 0
        
        if benchmark_type.lower().startswith('cpu'):
            # CPU-bound benchmark
            while time.time() - start_time < duration:
                _ = sum(i * i for i in range(1000))
                operations += 1
        
        elif benchmark_type.lower().startswith('memory'):
            # Memory-bound benchmark
            data = deque()
            while time.time() - start_time < duration:
                data.append([i for i in range(1000)])
                if len(data) > 100:
                    data.popleft()
                operations += 1
        
        else:
            # General benchmark
            while time.time() - start_time < duration:
                operations += 1
        
        elapsed = time.time() - start_time
        
        result = {
            'benchmark_type': benchmark_type,
            'duration': elapsed,
            'operations': operations,
            'operations_per_second': operations / elapsed if elapsed > 0 else 0,
            'start_time': start_time,
            'end_time': time.time()
        }
        
        self._benchmark_history.append(result)
        return result

    # ========================================================================
    # Background Collection
    # ========================================================================

    async def _background_collection_loop(self) -> None:
        """Background task to collect system metrics."""
        while not self._stop_collection.is_set():
            try:
                # Collect metrics
                await self._execute_system_monitoring()
                
                # Adjust interval based on memory pressure
                interval = self._collection_interval
                if self._current_memory_pressure == MemoryPressureLevel.ELEVATED:
                    interval *= 1.5
                elif self._current_memory_pressure == MemoryPressureLevel.HIGH:
                    interval *= 2.0
                elif self._current_memory_pressure == MemoryPressureLevel.CRITICAL:
                    interval *= 3.0
                
                # Wait with cancellation support
                try:
                    await asyncio.wait_for(
                        self._stop_collection.wait(),
                        timeout=interval
                    )
                except asyncio.TimeoutError:
                    pass  # Normal - continue loop
                    
            except Exception as e:
                logger.error(f"Background collection error: {e}")
                await asyncio.sleep(self._collection_interval)

    # ========================================================================
    # Alert Management
    # ========================================================================

    def _check_alerts(self, metrics: SystemMetrics) -> tuple[bool, Optional[str]]:
        """Check if any alert thresholds are breached."""
        if not self._alerts_enabled:
            return False, None
        
        alerts = []
        
        # Check CPU
        cpu_threshold = self._alert_thresholds.get('cpu_percent')
        if cpu_threshold and cpu_threshold.enabled:
            if metrics.cpu_percent >= cpu_threshold.critical:
                alerts.append(f"CRITICAL: CPU {metrics.cpu_percent:.1f}%")
            elif metrics.cpu_percent >= cpu_threshold.warning:
                alerts.append(f"WARNING: CPU {metrics.cpu_percent:.1f}%")
        
        # Check Memory
        memory_threshold = self._alert_thresholds.get('memory_percent')
        if memory_threshold and memory_threshold.enabled:
            if metrics.memory_percent >= memory_threshold.critical:
                alerts.append(f"CRITICAL: Memory {metrics.memory_percent:.1f}%")
            elif metrics.memory_percent >= memory_threshold.warning:
                alerts.append(f"WARNING: Memory {metrics.memory_percent:.1f}%")
        
        # Check Disk
        disk_threshold = self._alert_thresholds.get('disk_percent')
        if disk_threshold and disk_threshold.enabled:
            if metrics.disk_percent >= disk_threshold.critical:
                alerts.append(f"CRITICAL: Disk {metrics.disk_percent:.1f}%")
            elif metrics.disk_percent >= disk_threshold.warning:
                alerts.append(f"WARNING: Disk {metrics.disk_percent:.1f}%")
        
        if alerts:
            return True, " | ".join(alerts)
        return False, None

    def set_alert_threshold(
        self,
        metric: str,
        warning: float,
        critical: float,
        enabled: bool = True
    ) -> None:
        """Set alert threshold for a metric."""
        self._alert_thresholds[metric] = AlertThreshold(
            metric=metric,
            warning=warning,
            critical=critical,
            enabled=enabled
        )

    def enable_alerts(self, enabled: bool = True) -> None:
        """Enable or disable alerts."""
        self._alerts_enabled = enabled

    # ========================================================================
    # Metrics Access
    # ========================================================================

    def get_current_metrics(self) -> Optional[SystemMetrics]:
        """Get current system metrics."""
        return self._current_metrics

    def get_metrics_history(
        self,
        limit: int = 10,
        metric_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get historical system metrics."""
        entries = list(self._metrics_history)[-limit:]
        return [m.to_dict() for m in entries]

    def get_average_metrics(self, last_n: int = 10) -> Dict[str, float]:
        """Get average metrics over last N samples."""
        entries = list(self._metrics_history)[-last_n:]
        if not entries:
            return {}
        
        return {
            'avg_cpu_percent': sum(m.cpu_percent for m in entries) / len(entries),
            'avg_memory_percent': sum(m.memory_percent for m in entries) / len(entries),
            'avg_disk_percent': sum(m.disk_percent for m in entries) / len(entries),
            'avg_network_connections': sum(m.network_connections for m in entries) / len(entries),
        }

    def get_peak_metrics(self, last_n: int = 10) -> Dict[str, float]:
        """Get peak metrics over last N samples."""
        entries = list(self._metrics_history)[-last_n:]
        if not entries:
            return {}
        
        return {
            'peak_cpu_percent': max(m.cpu_percent for m in entries),
            'peak_memory_percent': max(m.memory_percent for m in entries),
            'peak_disk_percent': max(m.disk_percent for m in entries),
            'peak_network_connections': max(m.network_connections for m in entries),
        }

    # ========================================================================
    # Benchmark History
    # ========================================================================

    def get_benchmark_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent benchmark results."""
        return list(self._benchmark_history)[-limit:]

    def get_average_benchmark(self, benchmark_type: str) -> Optional[Dict[str, Any]]:
        """Get average benchmark results for a specific type."""
        entries = [
            b for b in self._benchmark_history
            if b.get('benchmark_type') == benchmark_type
        ]
        
        if not entries:
            return None
        
        return {
            'benchmark_type': benchmark_type,
            'avg_operations_per_second': sum(
                b.get('operations_per_second', 0) for b in entries
            ) / len(entries),
            'total_runs': len(entries)
        }

    # ========================================================================
    # Health Check
    # ========================================================================

    async def perform_health_check(self, detailed: bool = False) -> Dict[str, Any]:
        """Perform comprehensive health check."""
        health = {
            'status': 'healthy',
            'timestamp': time.time(),
            'checks': {}
        }
        
        # System resources
        if self._current_metrics:
            metrics = self._current_metrics
            health['checks']['resources'] = {
                'cpu_ok': metrics.cpu_percent < 90,
                'memory_ok': metrics.memory_percent < 90,
                'disk_ok': metrics.disk_percent < 95,
                'cpu_percent': metrics.cpu_percent,
                'memory_percent': metrics.memory_percent,
                'disk_percent': metrics.disk_percent
            }
        
        # Subsystems
        health['checks']['subsystems'] = {
            'advanced_monitoring': self._advanced_available,
            'watchdog': self._watchdog_available,
            'background_collection': self._collection_task is not None and not self._collection_task.done()
        }
        
        # Determine overall status
        if detailed:
            health['metrics_summary'] = self.get_average_metrics(5)
            health['peak_metrics'] = self.get_peak_metrics(5)
            health['collection_stats'] = {
                'total_collections': self._collections_count,
                'alerts_triggered': self._alerts_triggered,
                'health_checks': self._health_checks_performed
            }
        
        # Overall status
        resource_checks = health['checks'].get('resources', {})
        if not all([resource_checks.get('cpu_ok', True), 
                    resource_checks.get('memory_ok', True),
                    resource_checks.get('disk_ok', True)]):
            health['status'] = 'degraded'
        
        return health

    # ========================================================================
    # Security & Codebase Auditing (from tools/audit/)
    # ========================================================================

    async def run_security_audit(
        self,
        target_path: Optional[str] = None,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None
    ) -> Dict[str, Any]:
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
            report = await auditor.audit_directory(
                path=target,
                include_patterns=include_patterns or ['*.py', '*.js', '*.ts'],
                exclude_patterns=exclude_patterns or [
                    '**/node_modules/**', '**/.venv/**', '**/__pycache__/**',
                    '**/dist/**', '**/build/**', '**/tests/**'
                ]
            )
            
            return {
                'success': True,
                'target': target,
                'files_scanned': len(report.get('findings', {})),
                'issues_found': report.get('total_issues', 0),
                'critical_issues': report.get('critical_count', 0),
                'high_risk_issues': report.get('high_risk_count', 0),
                'security_score': report.get('security_score', 0),
                'issues': report.get('issues', []),
                'recommendations': report.get('recommendations', [])
            }
            
        except ImportError:
            logger.warning("SecurityAuditor module not available")
            return {'success': False, 'error': 'SecurityAuditor not available'}
        except Exception as e:
            logger.error(f"Security audit failed: {e}")
            return {'success': False, 'error': str(e)}

    async def verify_codebase_integrity(
        self,
        target_path: Optional[str] = None,
        min_lines_of_code: int = 5,
        strict_mode: bool = False
    ) -> Dict[str, Any]:
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
            from hledac.tools.diagnostics.codebase_integrity_validator import (
                CodebaseIntegrityValidator, ValidationConfig
            )
            
            config = ValidationConfig(
                min_lines_of_code=min_lines_of_code,
                strict_mode=strict_mode
            )
            
            validator = CodebaseIntegrityValidator(config)
            target = target_path or os.getcwd()
            
            result = validator.validate_directory(target)
            
            return {
                'success': True,
                'target': target,
                'files_analyzed': result['files_analyzed'],
                'issues_found': len(result.get('issues', [])),
                'integrity_score': result['integrity_score'],
                'quality_grade': result['quality_grade'],
                'dummy_functions': result.get('dummy_functions_count', 0),
                'stub_files': result.get('stub_files_count', 0),
                'issues': result.get('issues', [])[:20],  # Limit details
                'recommendations': result.get('recommendations', []),
                'passed': result['integrity_score'] >= 80
            }
            
        except ImportError:
            logger.warning("CodebaseIntegrityValidator not available")
            return {'success': False, 'error': 'Validator not available'}
        except Exception as e:
            logger.error(f"Codebase integrity check failed: {e}")
            return {'success': False, 'error': str(e)}

    async def verify_syntax_batch(
        self,
        target_path: Optional[str] = None,
        auto_fix: bool = True,
        parallel: bool = True
    ) -> Dict[str, Any]:
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
            from hledac.tools.audit.syntax_verifier import (
                SyntaxVerifier, VerificationConfig
            )
            
            config = VerificationConfig(
                auto_fix=auto_fix,
                parallel=parallel,
                max_workers=4
            )
            
            verifier = SyntaxVerifier(config)
            target = target_path or os.getcwd()
            
            result = verifier.verify_directory(target)
            
            return {
                'success': result.all_valid,
                'target': target,
                'files_checked': len(result.files_checked),
                'valid_files': result.valid_count,
                'invalid_files': result.invalid_count,
                'fixed_files': result.fixed_count,
                'errors': [
                    {'file': e.file, 'line': e.line, 'message': e.message}
                    for e in result.errors[:10]  # Limit details
                ],
                'all_valid': result.all_valid
            }
            
        except ImportError:
            logger.warning("SyntaxVerifier not available")
            return {'success': False, 'error': 'SyntaxVerifier not available'}
        except Exception as e:
            logger.error(f"Syntax verification failed: {e}")
            return {'success': False, 'error': str(e)}

    # ========================================================================
    # Reporting
    # ========================================================================

    def _get_feature_list(self) -> List[str]:
        """Report available features."""
        features = [
            "System metrics collection (psutil)",
            "Background metrics collection",
            "Historical metrics tracking",
            "Alert threshold management",
            "Performance benchmarking",
            "OWASP security auditing",
            "Codebase integrity validation",
            "Batch syntax verification"
        ]
        
        if self._advanced_available:
            features.append("Advanced system monitoring")
        if self._watchdog_available:
            features.append("Health check monitoring")
        
        features.extend([
            "Metrics aggregation and analysis",
            "Peak/average metrics calculation",
            "Comprehensive health checks",
            "Security vulnerability scanning",
            "Dummy/stub code detection"
        ])
        
        return features

    def get_monitoring_stats(self) -> Dict[str, Any]:
        """Get monitoring statistics."""
        return {
            'collections_count': self._collections_count,
            'alerts_triggered': self._alerts_triggered,
            'health_checks_performed': self._health_checks_performed,
            'metrics_history_size': len(self._metrics_history),
            'benchmark_history_size': len(self._benchmark_history),
            'background_collection_active': (
                self._collection_task is not None and 
                not self._collection_task.done()
            ),
            'current_memory_pressure': self._current_memory_pressure.value
        }

    def get_available_monitoring_systems(self) -> Dict[str, bool]:
        """Get availability status of all monitoring systems."""
        return {
            'advanced_monitoring': self._advanced_available,
            'watchdog': self._watchdog_available,
            'system_metrics': True,  # Always available via psutil
            'background_collection': (
                self._collection_task is not None and 
                not self._collection_task.done()
            )
        }

    # ========================================================================
    # Hermes3 Integration - Operation Tracking with Statistics
    # ========================================================================

    async def track_operation(
        self,
        operation_type: str,
        success: bool,
        duration: float,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Track operation with statistics (from Hermes3).
        
        Args:
            operation_type: Type of operation
            success: Whether operation succeeded
            duration: Execution duration in seconds
            metadata: Optional metadata
        """
        if operation_type not in self._operation_stats:
            self._operation_stats[operation_type] = {
                'total': 0,
                'successful': 0,
                'failed': 0,
                'avg_duration': 0.0,
                'total_duration': 0.0,
                'min_duration': float('inf'),
                'max_duration': 0.0,
                'last_executed': None
            }
        
        stats = self._operation_stats[operation_type]
        stats['total'] += 1
        stats['successful'] += 1 if success else 0
        stats['failed'] += 0 if success else 1
        stats['total_duration'] += duration
        stats['avg_duration'] = stats['total_duration'] / stats['total']
        stats['min_duration'] = min(stats['min_duration'], duration)
        stats['max_duration'] = max(stats['max_duration'], duration)
        stats['last_executed'] = time.time()
        
        # Also collect system metrics
        await self.collect_system_metrics()

    def get_operation_stats(
        self,
        operation_type: Optional[str] = None
    ) -> Dict[str, Any]:
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

    def get_health_status(self) -> Dict[str, Any]:
        """
        Get health status (from Hermes3).
        
        Returns:
            Health status with metrics
        """
        # Get latest metrics
        latest = self._current_metrics
        
        if latest is None:
            return {
                'status': 'unknown',
                'reason': 'No metrics collected yet'
            }
        
        # Determine health based on memory
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
        
        return {
            'status': health,
            'reason': reason,
            'cpu_percent': latest.cpu_percent,
            'memory_percent': latest.memory_percent,
            'memory_mb': latest.memory_used_mb,
            'collection_count': self._collections_count,
            'alerts_triggered': self._alerts_triggered
        }

    # ========================================================================
    # Diagnostics Engine Integration (from tools/preserved_logic/)
    # ========================================================================

    async def run_diagnostics(
        self,
        component: Optional[str] = None,
        auto_fix: bool = False
    ) -> Dict[str, Any]:
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
            from hledac.tools.preserved_logic.monitoring.diagnostics_engine import (
                DiagnosticsEngine, DiagnosticResult
            )
            
            engine = DiagnosticsEngine(
                enable_auto_diagnostics=False,  # Manual mode
                m1_optimization=True
            )
            
            issues = []
            
            if component:
                # Run diagnostic on specific component
                component_issues = await engine.run_manual_diagnostic(component)
                issues.extend(component_issues)
            else:
                # Run full diagnostic suite
                components = ["memory", "system", "network", "storage"]
                for comp in components:
                    comp_issues = await engine.run_manual_diagnostic(comp)
                    issues.extend(comp_issues)
            
            # Convert issues to dict format
            issues_dict = [
                {
                    'issue_id': issue.issue_id,
                    'component': issue.component,
                    'severity': issue.severity.value,
                    'description': issue.description,
                    'recommendations': issue.recommendations,
                    'resolved': issue.resolved,
                    'timestamp': issue.timestamp
                }
                for issue in issues
            ]
            
            # Critical issues count
            critical_count = sum(
                1 for i in issues 
                if i.severity.value in ['critical', 'error']
            )
            
            return {
                'success': True,
                'component': component or 'all',
                'issues_found': len(issues),
                'critical_issues': critical_count,
                'issues': issues_dict,
                'auto_fix_enabled': auto_fix,
                'recommendations': [
                    rec for issue in issues 
                    for rec in issue.recommendations
                ]
            }
            
        except ImportError:
            logger.warning("DiagnosticsEngine not available")
            return {
                'success': False,
                'error': 'DiagnosticsEngine not available',
                'component': component
            }
        except Exception as e:
            logger.error(f"Diagnostics failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'component': component
            }

    async def start_auto_diagnostics(
        self,
        interval_seconds: int = 60,
        enable_auto_fix: bool = False
    ) -> Dict[str, Any]:
        """
        Start automated diagnostics monitoring.
        
        Args:
            interval_seconds: Diagnostic check interval
            enable_auto_fix: Enable automatic issue fixing
            
        Returns:
            Start confirmation
        """
        try:
            from hledac.tools.preserved_logic.monitoring.diagnostics_engine import (
                DiagnosticsEngine
            )
            
            if not hasattr(self, '_diagnostics_engine'):
                self._diagnostics_engine = DiagnosticsEngine(
                    enable_auto_diagnostics=True,
                    diagnostic_interval=interval_seconds,
                    m1_optimization=True
                )
            
            success = await self._diagnostics_engine.start_diagnostics()
            
            return {
                'success': success,
                'interval_seconds': interval_seconds,
                'auto_fix_enabled': enable_auto_fix,
                'message': 'Auto-diagnostics started' if success else 'Already running'
            }
            
        except Exception as e:
            logger.error(f"Failed to start auto-diagnostics: {e}")
            return {'success': False, 'error': str(e)}

    async def stop_auto_diagnostics(self) -> Dict[str, Any]:
        """Stop automated diagnostics monitoring."""
        if hasattr(self, '_diagnostics_engine'):
            success = await self._diagnostics_engine.stop_diagnostics()
            return {'success': success, 'message': 'Auto-diagnostics stopped'}
        return {'success': False, 'message': 'Not running'}
