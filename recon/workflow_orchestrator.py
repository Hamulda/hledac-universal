"""Workflow Orchestrator for OSINT intelligence analysis.

Coordinates multiple analysis modules, correlates results, detects anomalies,
and generates comprehensive reports with risk assessment.









"""
import asyncio
import inspect
import json
import logging
import time
from dataclasses import dataclass, field
import msgspec
from datetime import UTC, datetime
from typing import Any
from hledac.universal.utils.asyncx import parallel_ok
from hledac.universal.utils.async_task import safe_create_task
from operator import attrgetter, itemgetter
logger = logging.getLogger(__name__)
MODULE_TIMEOUT = 60

class Finding(msgspec.Struct, gc=False):
    """Represents a finding from cross-module analysis.

    Attributes:
        finding_type: Type of finding (e.g., "pattern", "anomaly")
        description: Human-readable description of the finding
        severity: Severity level ("low", "medium", "high", "critical")
        confidence: Confidence score (0.0-1.0)
        modules: List of modules that contributed to this finding
    """
    finding_type: str
    description: str
    severity: str
    confidence: float
    modules: list[str] = field(default_factory=list)

class CorrelationReport(msgspec.Struct, frozen=True, gc=False):
    """Report of cross-module correlations.

    Attributes:
        cross_module_findings: List of findings from multiple modules
        risk_score: Calculated risk score (0.0-1.0)
        attribution: Attribution data (e.g., threat actor, source)
    """
    cross_module_findings: list[Finding] = field(default_factory=list)
    risk_score: float = 0.0
    attribution: dict[str, Any] = field(default_factory=dict)

class Anomaly(msgspec.Struct, frozen=True, gc=False):
    """Represents an anomaly detected during analysis.

    Attributes:
        anomaly_type: Type of anomaly detected
        severity: Severity level ("low", "medium", "high", "critical")
        description: Human-readable description
        affected_modules: List of modules where anomaly was detected
    """
    anomaly_type: str
    severity: str
    description: str
    affected_modules: list[str] = field(default_factory=list)

class SharedContext(msgspec.Struct, frozen=True, gc=False):
    """Shared context passed between workflow modules.

    Attributes:
        input_data: Original input data
        intermediate_results: Results from completed modules
        module_status: Status tracking for each module
        resource_usage: Resource usage statistics
    """
    input_data: Any = None
    intermediate_results: dict[str, Any] = field(default_factory=dict)
    module_status: dict[str, str] = field(default_factory=dict)
    resource_usage: dict[str, Any] = field(default_factory=dict)

class ComprehensiveReport(msgspec.Struct, frozen=True, gc=False):
    """Comprehensive analysis report from workflow execution.

    Attributes:
        input_summary: Summary of input data
        module_results: Results from each analysis module
        correlations: Cross-module correlation report
        anomalies: List of detected anomalies
        verdict: Final verdict ("CLEAN", "SUSPICIOUS", "HIGH_RISK")
        confidence: Overall confidence score
        recommendations: List of actionable recommendations
        timeline: Timeline of analysis events
        export_data: Data formatted for export
    """
    input_summary: dict[str, Any] = field(default_factory=dict)
    module_results: dict[str, Any] = field(default_factory=dict)
    correlations: CorrelationReport = field(default_factory=lambda: CorrelationReport())
    anomalies: list[Anomaly] = field(default_factory=list)
    verdict: str = 'CLEAN'
    confidence: float = 0.0
    recommendations: list[str] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    export_data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Export report as JSON string.

        Returns:
            JSON formatted report string
        """

        def serialize(obj: Any) -> Any:
            if isinstance(obj, datetime):
                return obj.isoformat()
            if isinstance(obj, (Finding, Anomaly)):
                return obj.__dict__
            if isinstance(obj, CorrelationReport):
                return {'cross_module_findings': [f.__dict__ for f in obj.cross_module_findings], 'risk_score': obj.risk_score, 'attribution': obj.attribution}
            if isinstance(obj, ComprehensiveReport):
                return {'input_summary': obj.input_summary, 'module_results': obj.module_results, 'correlations': serialize(obj.correlations), 'anomalies': [a.__dict__ for a in obj.anomalies], 'verdict': obj.verdict, 'confidence': obj.confidence, 'recommendations': obj.recommendations, 'timeline': obj.timeline, 'export_data': obj.export_data}
            return obj
        return json.dumps(serialize(self), indent=2, default=serialize)

    def to_markdown(self) -> str:
        """Export report as Markdown string.

        Returns:
            Markdown formatted report
        """
        lines = ['# Comprehensive Analysis Report', '', f'**Verdict:** {self.verdict}', f'**Confidence:** {self.confidence:.2%}', f'**Generated:** {datetime.now(UTC).isoformat()}', '', '## Input Summary', '']
        for key, value in self.input_summary.items():
            lines.append(f'- **{key}:** {value}')
        lines.extend(['', '## Module Results', ''])
        for module, result in self.module_results.items():
            lines.append(f'### {module}')
            lines.append(f'```json\n{json.dumps(result, indent=2, default=str)}\n```')
            lines.append('')
        lines.extend(['', '## Correlations', ''])
        lines.append(f'**Risk Score:** {self.correlations.risk_score:.2%}')
        lines.append('')
        for finding in self.correlations.cross_module_findings:
            lines.append(f'- **{finding.finding_type}** ({finding.severity}): {finding.description}')
        lines.extend(['', '## Anomalies', ''])
        for anomaly in self.anomalies:
            lines.append(f'- **{anomaly.anomaly_type}** ({anomaly.severity}): {anomaly.description}')
        lines.extend(['', '## Recommendations', ''])
        for i, rec in enumerate(self.recommendations, 1):
            lines.append(f'{i}. {rec}')
        return '\n'.join(lines)

    def to_html(self) -> str:
        """Export report as HTML string.

        Returns:
            HTML formatted report
        """
        verdict_class = {'CLEAN': 'success', 'SUSPICIOUS': 'warning', 'HIGH_RISK': 'danger'}.get(self.verdict, 'info')
        parts = [f"""<!DOCTYPE html>\n<html>\n<head>\n    <title>Analysis Report</title>\n    <style>\n        body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}\n        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; }}\n        .header {{ border-bottom: 2px solid #ddd; padding-bottom: 20px; margin-bottom: 30px; }}\n        .verdict {{ display: inline-block; padding: 10px 20px; border-radius: 4px; font-weight: bold; }}\n        .verdict.success {{ background: #d4edda; color: #155724; }}\n        .verdict.warning {{ background: #fff3cd; color: #856404; }}\n        .verdict.danger {{ background: #f8d7da; color: #721c24; }}\n        .section {{ margin: 30px 0; }}\n        .section h2 {{ color: #333; border-bottom: 1px solid #eee; padding-bottom: 10px; }}\n        .finding {{ padding: 10px; margin: 10px 0; background: #f8f9fa; border-left: 4px solid #007bff; }}\n        .anomaly {{ padding: 10px; margin: 10px 0; background: #fff3cd; border-left: 4px solid #ffc107; }}\n        .risk-score {{ font-size: 24px; font-weight: bold; color: {('#dc3545' if self.correlations.risk_score > 0.7 else '#ffc107' if self.correlations.risk_score > 0.3 else '#28a745')}; }}  # noqa: E501\n        pre {{ background: #f4f4f4; padding: 15px; border-radius: 4px; overflow-x: auto; }}\n        .recommendation {{ padding: 10px; margin: 5px 0; background: #e7f3ff; border-radius: 4px; }}\n    </style>\n</head>\n<body>\n    <div class="container">\n        <div class="header">\n            <h1>Comprehensive Analysis Report</h1>\n            <span class="verdict {verdict_class}">{self.verdict}</span>\n            <p><strong>Confidence:</strong> {self.confidence:.2%}</p>\n            <p><strong>Generated:</strong> {datetime.now(UTC).isoformat()}</p>  # noqa: DTZ005\n        </div>\n\n        <div class="section">\n            <h2>Risk Assessment</h2>\n            <div class="risk-score">Risk Score: {self.correlations.risk_score:.2%}</div>\n        </div>\n\n        <div class="section">\n            <h2>Input Summary</h2>\n            <ul>"""]
        for key, value in self.input_summary.items():
            parts.append(f'                <li><strong>{key}:</strong> {value}</li>')
        parts.extend(['            </ul>\n        </div>\n\n        <div class="section">\n            <h2>Correlations</h2>'])
        for finding in self.correlations.cross_module_findings:
            parts.append(f"""            <div class="finding">\n                <strong>{finding.finding_type}</strong> ({finding.severity})\n                <p>{finding.description}</p>\n                <small>Modules: {', '.join(finding.modules)}</small>\n            </div>""")
        parts.append('        </div>\n\n        <div class="section">\n            <h2>Anomalies</h2>')
        for anomaly in self.anomalies:
            parts.append(f"""            <div class="anomaly">\n                <strong>{anomaly.anomaly_type}</strong> ({anomaly.severity})\n                <p>{anomaly.description}</p>\n                <small>Affected: {', '.join(anomaly.affected_modules)}</small>\n            </div>""")
        parts.append('        </div>\n\n        <div class="section">\n            <h2>Recommendations</h2>')
        for rec in self.recommendations:
            parts.append(f'            <div class="recommendation">{rec}</div>')
        parts.append('        </div>\n\n        <div class="section">\n            <h2>Module Results</h2>')
        for module, result in self.module_results.items():
            parts.append(f'            <h3>{module}</h3>\n            <pre>{json.dumps(result, indent=2, default=str)}</pre>')
        parts.append('        </div>\n    </div>\n</body>\n</html>')
        return ''.join(parts)

class WorkflowPlan(msgspec.Struct, frozen=True, gc=False):
    """Plan for workflow execution.

    Attributes:
        modules: List of module names to execute
        execution_mode: "sequential" or "parallel"
        parallel_groups: Optional grouping for parallel execution
    """
    modules: list[str] = field(default_factory=list)
    execution_mode: str = 'parallel'
    parallel_groups: list[list[str]] | None = None

class IntelligenceConfig(msgspec.Struct, frozen=True, gc=False):
    """Configuration for workflow orchestrator.

    Attributes:
        module_timeout: Timeout per module in seconds
        max_parallel_modules: Maximum parallel modules
        enable_correlation: Whether to enable cross-module correlation
        enable_anomaly_detection: Whether to enable anomaly detection
        risk_thresholds: Risk score thresholds for verdicts
    """
    module_timeout: int = MODULE_TIMEOUT
    max_parallel_modules: int = 4
    enable_correlation: bool = True
    enable_anomaly_detection: bool = True
    risk_thresholds: dict[str, float] = field(default_factory=lambda: {'clean': 0.3, 'suspicious': 0.7})

class WorkflowOrchestrator:
    """Orchestrates multi-module analysis workflows.

    Coordinates execution of analysis modules, correlates results,
    detects anomalies, and generates comprehensive reports.

    Example:
        orchestrator = WorkflowOrchestrator(main_orchestrator)
        plan = WorkflowPlan(modules=["stego", "metadata", "encoding"])
        report = await orchestrator.execute_workflow(plan, input_data)
        print(report.to_json())
    """
    HIGH_RISK_PATTERNS = {('scrubbed_metadata', 'steganography_detected'): 0.5, ('dns_tunneling', 'encoded_payload'): 0.4, ('zero_width_unicode', 'base64_hidden'): 0.3, ('future_timestamp', 'gps_mismatch'): 0.2}
    __slots__ = tuple(('_execution_timeline', '_module_registry', 'config', 'orchestrator'))

    def __init__(self, orchestrator: Any, config: IntelligenceConfig | None=None):
        """Initialize workflow orchestrator.

        Args:
            orchestrator: Main orchestrator instance for module access
            config: Optional intelligence configuration
        """
        self.orchestrator = orchestrator
        self.config = config or IntelligenceConfig()
        self._module_registry: dict[str, Any] = {}
        self._execution_timeline: list[dict[str, Any]] = []

    def _add_timeline_event(self, event_type: str, details: dict[str, Any]) -> None:
        """Add event to execution timeline.

        Args:
            event_type: Type of event
            details: Event details
        """
        self._execution_timeline.append({'timestamp': datetime.now(UTC).isoformat(), 'type': event_type, 'details': details})

    async def execute_workflow(self, workflow: WorkflowPlan, input_data: Any) -> ComprehensiveReport:
        """Execute a workflow plan.

        Args:
            workflow: Workflow plan with module configuration
            input_data: Input data for analysis

        Returns:
            Comprehensive analysis report
        """
        start_time = time.time()
        self._execution_timeline = []
        self._add_timeline_event('workflow_start', {'modules': workflow.modules, 'mode': workflow.execution_mode})
        context = SharedContext(input_data=input_data, intermediate_results={}, module_status=dict.fromkeys(workflow.modules, 'pending'), resource_usage={})
        try:
            if workflow.execution_mode == 'parallel':
                groups = workflow.parallel_groups if workflow.parallel_groups else [[m] for m in workflow.modules]
                results = await self._execute_parallel(groups, input_data, context)
            else:
                results = await self._execute_sequential(workflow.modules, input_data, context)
            self._add_timeline_event('modules_complete', {'completed': len(results), 'failed': len(workflow.modules) - len(results)})
            correlations = CorrelationReport()
            if self.config.enable_correlation:
                correlations = self._correlate_results(results)
                self._add_timeline_event('correlation_complete', {'findings': len(correlations.cross_module_findings), 'risk_score': correlations.risk_score})
            anomalies: list[Anomaly] = []
            if self.config.enable_anomaly_detection:
                anomalies = self._detect_anomalies(results)
                self._add_timeline_event('anomaly_detection_complete', {'anomalies': len(anomalies)})
            report = self._generate_report(results, correlations, anomalies, context)
            duration = time.time() - start_time
            self._add_timeline_event('workflow_complete', {'duration_seconds': duration, 'verdict': report.verdict})
            report.timeline = self._execution_timeline
            return report
        except Exception as e:
            logger.error(f'Workflow execution failed: {e}')
            self._add_timeline_event('workflow_error', {'error': str(e)})
            raise

    async def _execute_sequential(self, modules: list[str], input_data: Any, context: SharedContext) -> dict[str, Any]:
        """Execute modules sequentially.

        Args:
            modules: List of module names
            input_data: Input data
            context: Shared execution context

        Returns:
            Dictionary of module results
        """
        results: dict[str, Any] = {}
        for module in modules:
            try:
                result = await self._execute_module(module, input_data, context)
                if result is not None:
                    results[module] = result
                    context.intermediate_results[module] = result
            except Exception as e:
                logger.error(f'Module {module} failed: {e}')
                context.module_status[module] = 'failed'
        return results

    async def _execute_parallel(self, module_groups: list[list[str]], input_data: Any, context: SharedContext) -> dict[str, Any]:
        """Execute modules in parallel groups.

        Args:
            module_groups: Groups of modules to execute in parallel
            input_data: Input data
            context: Shared execution context

        Returns:
            Dictionary of module results
        """
        results: dict[str, Any] = {}
        for group in module_groups:

            async def _run_with_timeout(module: str) -> Any:
                async with asyncio.timeout(self.config.module_timeout):
                    return await self._execute_module(module, input_data, context)
            tasks = [safe_create_task(_run_with_timeout(module), name=f'workflow:module:{module}') for module in group]
            group_results = await parallel_ok(*tasks, label='workflow_orchestrator:526')
            for module, result in zip(group, group_results, strict=False):
                if isinstance(result, Exception):
                    logger.error(f'Module {module} failed: {result}')
                    context.module_status[module] = 'failed'
                elif result is not None:
                    results[module] = result
                    context.intermediate_results[module] = result
        return results

    async def _execute_module(self, module: str, input_data: Any, context: SharedContext) -> Any:
        """Execute a single module.

        Args:
            module: Module name
            input_data: Input data
            context: Shared execution context

        Returns:
            Module execution result
        """
        context.module_status[module] = 'running'
        module_start = time.time()
        try:
            module_instance = self._get_module_instance(module)
            if module_instance is None:
                logger.warning(f'Module {module} not found')
                context.module_status[module] = 'not_found'
                return None
            if inspect.iscoroutinefunction(module_instance):
                async with asyncio.timeout(self.config.module_timeout):
                    result = await module_instance(input_data)
            elif hasattr(module_instance, 'analyze'):
                if inspect.iscoroutinefunction(module_instance.analyze):
                    async with asyncio.timeout(self.config.module_timeout):
                        result = await module_instance.analyze(input_data)
                else:
                    result = module_instance.analyze(input_data)
            elif hasattr(module_instance, 'process'):
                if inspect.iscoroutinefunction(module_instance.process):
                    async with asyncio.timeout(self.config.module_timeout):
                        result = await module_instance.process(input_data)
                else:
                    result = module_instance.process(input_data)
            else:
                result = {'error': f'No valid method found for {module}'}
            duration = time.time() - module_start
            context.module_status[module] = 'completed'
            context.resource_usage[module] = {'duration_seconds': duration}
            self._add_timeline_event('module_complete', {'module': module, 'duration_seconds': duration})
            return result
        except TimeoutError:
            logger.error(f'Module {module} timed out after {self.config.module_timeout}s')
            context.module_status[module] = 'timeout'
            return {'error': 'timeout', 'module': module}
        except Exception as e:
            logger.error(f'Module {module} error: {e}')
            context.module_status[module] = 'error'
            return {'error': str(e), 'module': module}

    def _get_module_instance(self, module: str) -> Any:
        """Get module instance from registry or orchestrator.

        Args:
            module: Module name

        Returns:
            Module instance or None
        """
        if module in self._module_registry:
            return self._module_registry[module]
        try:
            if hasattr(self.orchestrator, 'get_module') and callable(getattr(self.orchestrator, 'get_module', None)):
                get_module = getattr(self.orchestrator, 'get_module', None)
                if get_module is not None:
                    result = get_module(module)
                    if result is not None:
                        return result
        except Exception:  # noqa: BLE001
            pass
        try:
            attr = getattr(self.orchestrator, module, None)
            if attr is not None:
                return attr
        except Exception:  # noqa: BLE001
            pass
        return None

    def register_module(self, name: str, instance: Any) -> None:
        """Register a module instance.

        Args:
            name: Module name
            instance: Module instance
        """
        self._module_registry[name] = instance

    def _correlate_results(self, results: dict[str, Any]) -> CorrelationReport:
        """Correlate results across modules.

        Args:
            results: Dictionary of module results

        Returns:
            Correlation report with findings and risk score
        """
        findings: list[Finding] = []
        risk_score = 0.0
        attribution: dict[str, Any] = {}
        detected_patterns = set()
        for module, result in results.items():
            if isinstance(result, dict):
                for key in result.keys():
                    detected_patterns.add((module, key))
                if result.get('detected'):
                    detected_patterns.add((module, result.get('type', 'unknown')))
        for pattern, risk_increment in self.HIGH_RISK_PATTERNS.items():
            if pattern in detected_patterns or self._check_pattern(results, pattern):
                risk_score += risk_increment
                findings.append(Finding(finding_type='high_risk_correlation', description=f'Detected correlation: {pattern[0]} + {pattern[1]}', severity='high' if risk_increment >= 0.4 else 'medium', confidence=0.8, modules=list(results.keys())))
        indicators = self._extract_indicators(results)
        if len(indicators) > 1:
            risk_score += min(0.1 * (len(indicators) - 1), 0.3)
            findings.append(Finding(finding_type='multiple_indicators', description=f'Multiple suspicious indicators detected: {len(indicators)}', severity='medium', confidence=0.7, modules=list(results.keys())))
        attribution = self._extract_attribution(results)
        risk_score = min(risk_score, 1.0)
        return CorrelationReport(cross_module_findings=findings, risk_score=risk_score, attribution=attribution)

    def _check_pattern(self, results: dict[str, Any], pattern: tuple[str, str]) -> bool:
        """Check if a pattern exists in results.

        Args:
            results: Module results
            pattern: Pattern to check (module, indicator)

        Returns:
            True if pattern detected
        """
        module, indicator = pattern
        if module not in results:
            return False
        result = results[module]
        if isinstance(result, dict):
            return result.get('detected') or result.get('type') == indicator or indicator in str(result).lower()
        return False

    def _extract_indicators(self, results: dict[str, Any]) -> list[str]:
        """Extract suspicious indicators from results.

        Args:
            results: Module results

        Returns:
            List of indicator strings
        """
        indicators = []
        for module, result in results.items():
            if isinstance(result, dict):
                if result.get('suspicious') or result.get('detected'):
                    indicators.append(module)
                if result.get('indicators'):
                    indicators.extend(result['indicators'])
        return indicators

    def _extract_attribution(self, results: dict[str, Any]) -> dict[str, Any]:
        """Extract attribution information from results.

        Args:
            results: Module results

        Returns:
            Attribution dictionary
        """
        attribution = {}
        for module, result in results.items():
            if isinstance(result, dict):
                if result.get('attribution'):
                    attribution[module] = result['attribution']
                if result.get('source'):
                    attribution['source'] = result['source']
        return attribution

    def _check_missing_data(self, results: dict[str, Any]) -> list[Anomaly]:
        """Check for missing data anomalies."""
        anomalies: list[Anomaly] = []
        for module, result in results.items():
            if isinstance(result, dict) and result.get('error'):
                anomalies.append(Anomaly(
                    anomaly_type='module_failure',
                    severity='medium',
                    description=f"Module {module} failed: {result['error']}",
                    affected_modules=[module]
                ))
        return anomalies

    def _detect_anomalies(self, results: dict[str, Any]) -> list[Anomaly]:
        """Detect anomalies in module results."""
        anomalies: list[Anomaly] = []
        anomalies.extend(self._check_missing_data(results))
        anomalies.extend(self._check_low_confidence(results))
        anomalies.extend(self._check_timing_anomalies(results))
        return anomalies

    def _check_low_confidence(self, results: dict[str, Any]) -> list[Anomaly]:
        """Check for low confidence anomalies."""
        anomalies: list[Anomaly] = []
        confidence_values = []
        for result in results.values():
            if isinstance(result, dict) and result.get('confidence'):
                confidence_values.append(result['confidence'])
        if len(confidence_values) > 1:
            try:
                import statistics
                variance = statistics.variance(confidence_values)
                if variance > 0.2:
                    anomalies.append(Anomaly(
                        anomaly_type='high_confidence_variance',
                        severity='low',
                        description=f'High variance in module confidence: {variance:.2f}',
                        affected_modules=list(results.keys())
                    ))
            except Exception:
                pass
        return anomalies

    def _detect_anomalies(self, results: dict[str, Any]) -> list[Anomaly]:
        """Detect anomalies in module results."""
        anomalies: list[Anomaly] = []
        anomalies.extend(self._check_missing_data(results))
        anomalies.extend(self._check_low_confidence(results))
        anomalies.extend(self._check_timing_anomalies(results))
        return anomalies

    def _check_timing_anomalies(self, results: dict[str, Any]) -> list[Anomaly]:
        """Check for timing-related anomalies."""
        anomalies: list[Anomaly] = []
        timestamps = []
        for module, result in results.items():
            if isinstance(result, dict) and result.get('timestamp'):
                try:
                    ts = datetime.fromisoformat(result['timestamp'])
                    timestamps.append((module, ts))
                except (ValueError, TypeError):  # noqa: BLE001
                    pass
        if len(timestamps) > 1:
            now = datetime.now(UTC)
            for module, ts in timestamps:
                if ts > now:
                    anomalies.append(Anomaly(
                        anomaly_type='future_timestamp',
                        severity='high',
                        description=f'Future timestamp detected in {module}',
                        affected_modules=[module]
                    ))
        return anomalies

    def _detect_anomalies(self, results: dict[str, Any]) -> list[Anomaly]:
        """Detect anomalies in module results."""
        anomalies: list[Anomaly] = []
        anomalies.extend(self._check_missing_data(results))
        anomalies.extend(self._check_low_confidence(results))
        anomalies.extend(self._check_timing_anomalies(results))
        return anomalies

    def _generate_report(self, results: dict[str, Any], correlations: CorrelationReport, anomalies: list[Anomaly], context: SharedContext) -> ComprehensiveReport:
        """Generate comprehensive report.

        Args:
            results: Module results
            correlations: Correlation report
            anomalies: Detected anomalies
            context: Shared execution context

        Returns:
            Comprehensive analysis report
        """
        input_summary = {'type': type(context.input_data).__name__, 'size': len(str(context.input_data)) if context.input_data else 0, 'modules_executed': len(results), 'execution_mode': 'parallel' if context.module_status else 'sequential'}
        confidence_values = []
        for result in results.values():
            if isinstance(result, dict) and result.get('confidence'):
                confidence_values.append(result['confidence'])
        overall_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.5
        recommendations = self._generate_recommendations(results, correlations, anomalies)
        verdict = self._get_verdict(correlations.risk_score)
        export_data = {'version': '1.0', 'generated_at': datetime.now(UTC).isoformat(), 'total_modules': len(context.module_status), 'successful_modules': len(results), 'risk_score': correlations.risk_score}
        return ComprehensiveReport(input_summary=input_summary, module_results=results, correlations=correlations, anomalies=anomalies, verdict=verdict, confidence=overall_confidence, recommendations=recommendations, timeline=self._execution_timeline, export_data=export_data)

    def _recommend_high_risk(self, correlations: CorrelationReport) -> list[str]:
        """Generate recommendations for high risk scenarios."""
        return ['HIGH RISK: Immediate investigation recommended. Multiple suspicious indicators detected.']

    def _recommend_suspicious(self, correlations: CorrelationReport) -> list[str]:
        """Generate recommendations for suspicious scenarios."""
        return ['SUSPICIOUS: Further analysis recommended. Some indicators warrant closer examination.']

    def _recommend_from_anomalies(self, anomalies: list[Anomaly]) -> list[str]:
        """Generate recommendations from detected anomalies."""
        recs = []
        for anomaly in anomalies:
            if anomaly.anomaly_type == 'future_timestamp':
                recs.append('Verify system clock and timestamp sources. Future timestamps may indicate manipulation.')
            elif anomaly.anomaly_type == 'module_failure':
                recs.append(f'Re-run failed module: {anomaly.affected_modules[0]}. Results may be incomplete.')
        return recs

    def _recommend_from_results(self, results: dict[str, Any]) -> list[str]:
        """Generate recommendations from module results."""
        recs = []
        for module, result in results.items():
            if isinstance(result, dict):
                if result.get('recommendations'):
                    recs.extend(result['recommendations'])
                if result.get('detected') and module == 'steganography':
                    recs.append('Extract and analyze hidden content using specialized tools.')
                if result.get('detected') and module == 'metadata':
                    recs.append('Review metadata for OPSEC violations and attribution clues.')
        return recs

    def _generate_recommendations(self, results: dict[str, Any], correlations: CorrelationReport, anomalies: list[Anomaly]) -> list[str]:
        """Generate actionable recommendations."""
        recommendations = []
        if correlations.risk_score >= 0.7:
            recommendations.extend(self._recommend_high_risk(correlations))
        elif correlations.risk_score >= 0.3:
            recommendations.extend(self._recommend_suspicious(correlations))
        recommendations.extend(self._recommend_from_anomalies(anomalies))
        recommendations.extend(self._recommend_from_results(results))
        seen = set()
        unique_recommendations = []
        for rec in recommendations:
            if rec not in seen:
                seen.add(rec)
                unique_recommendations.append(rec)
        return unique_recommendations

    def _get_verdict(self, risk_score: float) -> str:
        """Determine verdict based on risk score.

        Args:
            risk_score: Calculated risk score (0.0-1.0)

        Returns:
            Verdict string ("CLEAN", "SUSPICIOUS", or "HIGH_RISK")
        """
        clean_threshold = self.config.risk_thresholds.get('clean', 0.3)
        suspicious_threshold = self.config.risk_thresholds.get('suspicious', 0.7)
        if risk_score < clean_threshold:
            return 'CLEAN'
        elif risk_score < suspicious_threshold:
            return 'SUSPICIOUS'
        else:
            return 'HIGH_RISK'
HIGH_RISK_PATTERNS: dict[tuple[str, str], float] = {('scrubbed_metadata', 'steganography_detected'): 0.5, ('dns_tunneling', 'encoded_payload'): 0.4, ('zero_width_unicode', 'base64_hidden'): 0.3, ('future_timestamp', 'gps_mismatch'): 0.2}
SEVERITY_WEIGHTS = {'critical': 1.0, 'high': 0.75, 'medium': 0.5, 'low': 0.25}

class CorrelationResult(msgspec.Struct, frozen=True, gc=False):
    """Lightweight correlation result from findings analysis.

    Attributes:
        themes: Grouped findings by correlation theme
        risk_score: Overall risk score (0.0-1.0)
        risk_buckets: Findings bucketed by risk level
        top_themes: Top 5 most significant themes sorted by weight
        anomaly_count: Number of detected anomalies
        verdict: Risk verdict string

        # --- NEW: actionable condensation ---
        source_themes: dict[str, list[str]]           # source -> list of theme keys
        top_entities: list[dict[str, Any]]            # extracted IOCs (domain/ip/hash/url)
        repeated_domains: list[str]                   # domains seen across >1 finding
        repeated_iocs: list[dict[str, Any]]          # IOCs appearing >1 time
        dominant_cluster: str | None               # theme with most high-severity findings
        high_risk_branch: list[dict[str, Any]]        # critical/high findings with infra hints
        theme_source_overlap: dict[str, list[str]]   # theme -> sources contributing
        campaign_hints: list[dict[str, Any]]          # findings suggesting same campaign
        coupling_pairs: list[tuple[str, str]]          # (entity, related_entity) pairs
        so_what: str                                   # one-liner operator takeaway

        # --- SECOND-ORDER CONDENSATION (sprint delta) ---
        cross_source_confidence: float = 0.0       # 0.0-1.0: multi-source corroboration score
        corroborated_iocs: list[dict[str, Any]] = field(default_factory=list)  # IOCs with 2+ source evidence
        top_priority_pivots: list[dict[str, Any]] = field(default_factory=list)  # bounded action shortlist
        campaign_confidence: float = 0.0            # 0.0-1.0: campaign cluster confidence
    """
    themes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    risk_score: float = 0.0
    risk_buckets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    top_themes: list[tuple[str, float]] = field(default_factory=list)
    anomaly_count: int = 0
    verdict: str = 'CLEAN'
    source_themes: dict[str, list[str]] = field(default_factory=dict)
    top_entities: list[dict[str, Any]] = field(default_factory=list)
    repeated_domains: list[str] = field(default_factory=list)
    repeated_iocs: list[dict[str, Any]] = field(default_factory=list)
    dominant_cluster: str | None = None
    high_risk_branch: list[dict[str, Any]] = field(default_factory=list)
    theme_source_overlap: dict[str, list[str]] = field(default_factory=dict)
    campaign_hints: list[dict[str, Any]] = field(default_factory=list)
    coupling_pairs: list[tuple[str, str]] = field(default_factory=list)
    so_what: str = ''
    cross_source_confidence: float = 0.0
    corroborated_iocs: list[dict[str, Any]] = field(default_factory=list)
    top_priority_pivots: list[dict[str, Any]] = field(default_factory=list)
    campaign_confidence: float = 0.0
    what_matters_first: str = ''
    operator_shortlist: list[dict[str, Any]] = field(default_factory=list)
    confidence_note: str = ''
    signal_quality: str = 'weak'

def _normalize_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize finding dicts to consistent schema."""
    normalized: list[dict[str, Any]] = []
    for f in findings:
        nf: dict[str, Any] = {
            'type': f.get('type') or f.get('finding_type') or f.get('indicator_type', 'unknown'),
            'severity': f.get('severity', 'medium'),
            'confidence': float(f.get('confidence', 0.5)),
            'description': f.get('description') or f.get('description_text', ''),
            'source': f.get('source') or f.get('module') or f.get('tag') or f.get('tags', ['unknown']),
        }
        if isinstance(nf['source'], list):
            nf['source'] = nf['source'][0] if nf['source'] else 'unknown'
        normalized.append(nf)
    return normalized


def _calculate_risk_score(normalized: list[dict[str, Any]]) -> float:
    """Calculate risk score from normalized findings."""
    risk_score = 0.0
    for f in normalized:
        severity = f['severity'].lower()
        weight = SEVERITY_WEIGHTS.get(severity, 0.25)
        risk_score += weight * f['confidence']
    return min(risk_score / max(len(normalized), 1), 1.0)


def _group_themes(normalized: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group normalized findings by theme key."""
    themes: dict[str, list[dict[str, Any]]] = {}
    for f in normalized:
        theme_key = _derive_theme_key(f)
        if theme_key not in themes:
            themes[theme_key] = []
        themes[theme_key].append(f)
    return themes


def _calculate_theme_weights(themes: dict[str, list[dict[str, Any]]]) -> dict[str, float]:
    """Calculate weights for each theme based on severity and confidence."""
    theme_weights: dict[str, float] = {}
    for theme, theme_findings in themes.items():
        weights = [SEVERITY_WEIGHTS.get(x['severity'].lower(), 0.25) * x['confidence'] for x in theme_findings]
        theme_weights[theme] = sum(weights) / max(len(weights), 1)
    return theme_weights


def _determine_verdict(risk_score: float, thresholds: dict[str, float]) -> str:
    """Determine verdict based on risk score and thresholds."""
    if risk_score >= thresholds.get('suspicious', 0.7):
        return 'HIGH_RISK'
    elif risk_score >= thresholds.get('clean', 0.3):
        return 'SUSPICIOUS'
    return 'CLEAN'


def _build_source_themes(normalized: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Build mapping from source to list of theme keys."""
    source_themes: dict[str, list[str]] = {}
    for f in normalized:
        src = f['source']
        tk = _derive_theme_key(f)
        if src not in source_themes:
            source_themes[src] = []
        if tk not in source_themes[src]:
            source_themes[src].append(tk)
    return source_themes


def _normalize_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize finding dicts to consistent schema."""
    normalized: list[dict[str, Any]] = []
    for f in findings:
        nf: dict[str, Any] = {
            'type': f.get('type') or f.get('finding_type') or f.get('indicator_type', 'unknown'),
            'severity': f.get('severity', 'medium'),
            'confidence': float(f.get('confidence', 0.5)),
            'description': f.get('description') or f.get('description_text', ''),
            'source': f.get('source') or f.get('module') or f.get('tag') or f.get('tags', ['unknown']),
        }
        if isinstance(nf['source'], list):
            nf['source'] = nf['source'][0] if nf['source'] else 'unknown'
        normalized.append(nf)
    return normalized


def _compute_risk_score(normalized: list[dict[str, Any]]) -> float:
    """Calculate risk score from normalized findings."""
    risk_score = 0.0
    for f in normalized:
        severity = f['severity'].lower()
        weight = SEVERITY_WEIGHTS.get(severity, 0.25)
        risk_score += weight * f['confidence']
    return min(risk_score / max(len(normalized), 1), 1.0)


def _group_themes(normalized: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group normalized findings by theme key."""
    themes: dict[str, list[dict[str, Any]]] = {}
    for f in normalized:
        theme_key = _derive_theme_key(f)
        if theme_key not in themes:
            themes[theme_key] = []
        themes[theme_key].append(f)
    return themes


def _calculate_theme_weights(themes: dict[str, list[dict[str, Any]]]) -> dict[str, float]:
    """Calculate weights for each theme based on severity and confidence."""
    theme_weights: dict[str, float] = {}
    for theme, theme_findings in themes.items():
        weights = [SEVERITY_WEIGHTS.get(x['severity'].lower(), 0.25) * x['confidence'] for x in theme_findings]
        theme_weights[theme] = sum(weights) / max(len(weights), 1)
    return theme_weights


def _build_risk_buckets(normalized: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Build risk buckets from normalized findings."""
    buckets: dict[str, list[dict[str, Any]]] = {'critical': [], 'high': [], 'medium': [], 'low': []}
    for f in normalized:
        sev = f['severity'].lower()
        if sev in buckets:
            buckets[sev].append(f)
    return buckets


def _build_source_themes(normalized: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Build mapping from source to list of theme keys."""
    source_themes: dict[str, list[str]] = {}
    for f in normalized:
        src = f['source']
        tk = _derive_theme_key(f)
        if src not in source_themes:
            source_themes[src] = []
        if tk not in source_themes[src]:
            source_themes[src].append(tk)
    return source_themes


def _extract_and_correlate_entities(findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Extract entities and compute repeated domains/IOCs."""
    all_entities, domain_counts, ioc_counts = _extract_entities(findings)
    repeated_domains = [d for d, cnt in domain_counts.items() if cnt > 1]
    repeated_iocs = [{'value': v, 'type': t, 'count': c} for (v, t), c in ioc_counts.items() if c > 1]
    return all_entities, repeated_domains, repeated_iocs


def _compute_cluster_metrics(themes: dict[str, list[dict[str, Any]]], normalized: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Compute dominant cluster and high-risk branch."""
    dominant_cluster = None
    cluster_scores: dict[str, float] = {}
    for theme, fndgs in themes.items():
        score = sum((SEVERITY_WEIGHTS.get(x['severity'].lower(), 0.25) for x in fndgs if x['severity'].lower() in ('critical', 'high')))
        if score > 0:
            cluster_scores[theme] = score
    if cluster_scores:
        dominant_cluster = max(cluster_scores, key=lambda k: cluster_scores.get(k, 0.0))
    high_risk_branch = [f for f in normalized if f['severity'].lower() in ('critical', 'high') and _has_infra_hints(f)]
    return dominant_cluster, high_risk_branch


def _compute_theme_source_overlap(themes: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    """Compute theme-source overlap mapping."""
    theme_source_overlap: dict[str, list[str]] = {}
    for theme, fndgs in themes.items():
        srcs = list({x['source'] for x in fndgs})
        theme_source_overlap[theme] = srcs
    return theme_source_overlap


def _compute_correlation_signals(normalized: list[dict[str, Any]], theme_source_overlap: dict[str, list[str]], themes: dict[str, list[dict[str, Any]]]) -> tuple[float, list[dict[str, Any]]]:
    """Compute cross-source confidence and corroboration signals."""
    _cross_src_conf = _calc_cross_source_confidence(normalized, theme_source_overlap, [])
    campaign_hints = _find_campaign_hints(normalized, themes)
    return _cross_src_conf, campaign_hints


def _compute_top_priority_items(normalized: list[dict[str, Any]], repeated_iocs: list[dict[str, Any]], theme_source_overlap: dict[str, list[str]], campaign_hints: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    """Compute top priority pivots and campaign confidence."""
    _corr_iocs = _get_corroborated_iocs(normalized, repeated_iocs)
    _top_pivots = _get_top_priority_pivots(normalized, None, [], [])
    _camp_conf = _calc_campaign_confidence(campaign_hints, theme_source_overlap)
    return _corr_iocs, _camp_conf


def _build_final_signals(normalized: list[dict[str, Any]], _corr_iocs: list[dict[str, Any]], _camp_conf: float, campaign_hints: list[dict[str, Any]], theme_source_overlap: dict[str, list[str]]) -> tuple[str, list[dict[str, Any]], str, str]:
    """Build final signal quality and confidence metrics."""
    what_matters = _get_what_matters_first('SUSPICIOUS', None, [], _corr_iocs)
    operator_list = _build_operator_shortlist(None, [], _corr_iocs, 0.0)
    confidence_note = _build_confidence_note(0.0, 0.0, _camp_conf)
    signal_quality = _classify_signal_quality(0.0, 0.0, _camp_conf, _corr_iocs)
    return what_matters, operator_list, confidence_note, signal_quality


def _build_correlation_result(
    normalized: list[dict[str, Any]],
    themes: dict[str, list[dict[str, Any]]],
    theme_weights: dict[str, float],
    buckets: dict[str, list[dict[str, Any]]],
    max_themes: int,
    verdict: str,
    source_themes: dict[str, list[str]],
    all_entities: list[dict[str, Any]],
    repeated_domains: list[str],
    repeated_iocs: list[dict[str, Any]],
    dominant_cluster: str | None,
    high_risk_branch: list[dict[str, Any]],
    theme_source_overlap: dict[str, list[str]],
    campaign_hints: list[dict[str, Any]],
    _corr_iocs: list[dict[str, Any]],
    _top_pivots: list[dict[str, Any]],
    _camp_conf: float,
) -> CorrelationResult:
    """Build the final CorrelationResult with all computed data."""
    sorted_themes = sorted(theme_weights.items(), key=lambda x: -x[1])
    top_themes = sorted_themes[:max_themes]
    coupling_pairs = _find_coupling_pairs(all_entities)
    so_what = _build_so_what(verdict, 0.0, top_themes, dominant_cluster, len(high_risk_branch), 0, repeated_domains)
    _cross_src_conf = _calc_cross_source_confidence(normalized, theme_source_overlap, campaign_hints)
    what_matters, operator_list, confidence_note, signal_quality = _build_final_signals(
        normalized, _corr_iocs, _camp_conf, campaign_hints, theme_source_overlap
    )
    return CorrelationResult(
        themes=themes, risk_score=0.0, risk_buckets=buckets, top_themes=top_themes,
        anomaly_count=_count_anomalies(normalized), verdict=verdict, source_themes=source_themes,
        top_entities=[], repeated_domains=repeated_domains, repeated_iocs=repeated_iocs,
        dominant_cluster=dominant_cluster, high_risk_branch=high_risk_branch,
        theme_source_overlap=theme_source_overlap, campaign_hints=campaign_hints,
        coupling_pairs=coupling_pairs, so_what=so_what, cross_source_confidence=_cross_src_conf,
        corroborated_iocs=_corr_iocs, top_priority_pivots=_top_pivots, campaign_confidence=_camp_conf,
        what_matters_first=what_matters, operator_shortlist=operator_list,
        confidence_note=confidence_note, signal_quality=signal_quality
    )


def correlate_findings(findings: list[dict[str, Any]], *, risk_thresholds: dict[str, float] | None=None, max_themes: int=10) -> CorrelationResult:
    """Correlate findings and produce grouped themes with risk scoring.

    Pure function - no side effects, no storage, no orchestrator dependency.
    Works with finding-like dicts, IOC dicts, or any dict with:
        - type / finding_type / indicator_type
        - severity (critical/high/medium/low)
        - confidence (0.0-1.0)
        - description / description_text
        - source / module / tag / tags

    Args:
        findings: List of finding dictionaries
        risk_thresholds: Optional custom risk thresholds
        max_themes: Maximum number of themes to return (default 10)

    Returns:
        CorrelationResult with themes, risk_score, buckets, top_themes

    Example:
        findings = [
            {"type": "ioc", "severity": "high", "confidence": 0.9,
             "description": "Malicious domain found", "source": "dns"},
            {"type": "pattern", "severity": "medium", "confidence": 0.7,
             "description": "Suspicious encoding", "source": "encoding"},
        ]
        result = correlate_findings(findings)
        # result.themes, result.risk_score, result.risk_buckets, result.top_themes
    """
    if not findings:
        return CorrelationResult()
    thresholds = risk_thresholds or {'clean': 0.3, 'suspicious': 0.7}

    # Normalize and compute basic metrics
    normalized = _normalize_findings(findings)
    risk_score = _compute_risk_score(normalized)
    themes = _group_themes(normalized)
    theme_weights = _calculate_theme_weights(themes)
    buckets = _build_risk_buckets(normalized)
    verdict = _determine_verdict(risk_score, thresholds)
    source_themes = _build_source_themes(normalized)

    # Extract entities and compute correlations
    all_entities, repeated_domains, repeated_iocs = _extract_and_correlate_entities(findings)
    dominant_cluster, high_risk_branch = _compute_cluster_metrics(themes, normalized)
    theme_source_overlap = _compute_theme_source_overlap(themes)
    campaign_hints = _find_campaign_hints(normalized, themes)
    _corr_iocs, _camp_conf = _compute_top_priority_items(normalized, repeated_iocs, theme_source_overlap, campaign_hints)

    return CorrelationResult(
        themes=themes, risk_score=risk_score, risk_buckets=buckets,
        top_themes=sorted(theme_weights.items(), key=lambda x: -x[1])[:max_themes],
        anomaly_count=_count_anomalies(normalized), verdict=verdict, source_themes=source_themes,
        top_entities=sorted(all_entities, key=attrgetter("get")('_weight', 0), reverse=True)[:20],
        repeated_domains=repeated_domains, repeated_iocs=repeated_iocs,
        dominant_cluster=dominant_cluster, high_risk_branch=high_risk_branch,
        theme_source_overlap=theme_source_overlap, campaign_hints=campaign_hints,
        coupling_pairs=_find_coupling_pairs(all_entities),
        so_what=_build_so_what(verdict, risk_score, [], dominant_cluster, len(high_risk_branch), 0, repeated_domains),
        cross_source_confidence=_calc_cross_source_confidence(normalized, theme_source_overlap, campaign_hints),
        corroborated_iocs=_corr_iocs, top_priority_pivots=_get_top_priority_pivots(normalized, dominant_cluster, high_risk_branch, repeated_domains),
        campaign_confidence=_camp_conf,
        what_matters_first=_get_what_matters_first(verdict, dominant_cluster, high_risk_branch, _corr_iocs),
        operator_shortlist=_build_operator_shortlist(dominant_cluster, high_risk_branch, _corr_iocs, risk_score),
        confidence_note=_build_confidence_note(risk_score, 0.0, _camp_conf),
        signal_quality=_classify_signal_quality(risk_score, 0.0, _camp_conf, _corr_iocs)
    )

def _extract_entities(findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int], dict[tuple[str, str], int]]:
    """Extract IOCs (domains, IPs, hashes, URLs) from findings descriptions.

    Returns:
        (entities, domain_counts, ioc_counts)
        domain_counts: domain -> count across findings
        ioc_counts: (value, type) -> count across findings
    """
    entities: list[dict[str, Any]] = []
    domain_counts: dict[str, int] = {}
    ioc_counts: dict[tuple[str, str], int] = {}
    from hledac.universal.core.ioc_patterns import DOMAIN_RE, IPV4_RE, HASH_RE, URL_RE
    for f in findings:
        text = f.get('description', '') + ' ' + f.get('type', '')
        severity = f.get('severity', 'medium')
        confidence = f.get('confidence', 0.5)
        weight = SEVERITY_WEIGHTS.get(severity.lower(), 0.25) * confidence
        found: dict[str, Any] = {}
        for domain in DOMAIN_RE.findall(text):
            domain_lower = domain.lower()
            found[domain_lower] = {'value': domain_lower, 'type': 'domain', '_weight': weight}
            domain_counts[domain_lower] = domain_counts.get(domain_lower, 0) + 1
        for ip in IPV4_RE.findall(text):
            found[ip] = {'value': ip, 'type': 'ipv4', '_weight': weight}
        for h in HASH_RE.findall(text):
            found[h] = {'value': h, 'type': 'hash', '_weight': weight}
        for url in URL_RE.findall(text):
            found[url] = {'value': url, 'type': 'url', '_weight': weight}
        for ent in found.values():
            key = (ent['value'], ent['type'])
            ioc_counts[key] = ioc_counts.get(key, 0) + 1
            entities.append(ent)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for e in entities:
        k = (e['value'], e['type'])
        if k not in seen:
            seen.add(k)
            deduped.append(e)
    return (deduped, domain_counts, ioc_counts)

def _has_infra_hints(finding: dict[str, Any]) -> bool:
    """Check if finding has infrastructure-related hints."""
    text = (finding.get('description', '') + ' ' + finding.get('type', '')).lower()
    hints = ('domain', 'dns', 'ip', 'c2', 'command', 'control', 'server', 'host', 'infrastructure', 'tunnel', 'callback', 'beacon')
    return any((h in text for h in hints))

def _find_campaign_hints(findings: list[dict[str, Any]], _themes: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Find findings that may belong to the same campaign.

    Heuristic: same type appearing from multiple sources or
    high confidence + high severity cluster.
    """
    hints: list[dict[str, Any]] = []
    type_sources: dict[str, set[str]] = {}
    for f in findings:
        type_sources.setdefault(f['type'], set()).add(f['source'])
    for ftype, srcs in type_sources.items():
        if len(srcs) >= 2:
            matching = [f for f in findings if f['type'] == ftype]
            avg_conf = sum((x['confidence'] for x in matching)) / max(len(matching), 1)
            hints.append({'type': 'multi_source_cluster', 'finding_type': ftype, 'sources': list(srcs), 'count': len(matching), 'avg_confidence': round(avg_conf, 2)})
    high_conf_findings = [f for f in findings if f['confidence'] > 0.8 and f['severity'].lower() in ('high', 'critical')]
    if len(high_conf_findings) >= 2:
        hints.append({'type': 'high_confidence_cluster', 'count': len(high_conf_findings), 'severities': [f['severity'] for f in high_conf_findings]})
    return hints

def _find_coupling_pairs(entities: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Find entity pairs that appear in the same finding.

    Returns list of (entity1_value, entity2_value) tuples.
    """
    pairs: list[tuple[str, str]] = []
    by_type: dict[str, list[str]] = {}
    for e in entities:
        by_type.setdefault(e['type'], []).append(e['value'])
    for dtype, dvals in list(by_type.items())[:2]:
        for itype, ivals in list(by_type.items())[1:]:
            for dv in dvals[:5]:
                for iv in ivals[:5]:
                    pairs.append((dv, iv))
    return list(set(pairs))[:20]

def _build_so_what(verdict: str, risk_score: float, top_themes: list[tuple[str, float]], dominant_cluster: str | None, high_risk_count: int, anomaly_count: int, repeated_domains: list[str]) -> str:
    """Build one-liner operator takeaway."""
    if verdict == 'HIGH_RISK':
        parts = ['HIGH RISK detected']
        if dominant_cluster:
            parts.append(f'cluster={dominant_cluster}')
        if high_risk_count > 0:
            parts.append(f'{high_risk_count} critical/high findings')
        if anomaly_count > 0:
            parts.append(f'{anomaly_count} anomalies')
        if repeated_domains:
            parts.append(f"repeated domains: {', '.join(repeated_domains[:3])}")
        return '; '.join(parts)
    elif verdict == 'SUSPICIOUS':
        if top_themes:
            top = top_themes[0][0]
            return f'SUSPICIOUS: top theme={top}'
        return 'SUSPICIOUS: review recommended'
    else:
        return 'CLEAN: no significant threats detected'

def _derive_theme_key(finding: dict[str, Any]) -> str:
    """Derive theme key from finding for grouping."""
    ftype = finding.get('type', 'unknown').lower()
    source = str(finding.get('source', 'unknown')).lower()
    if any((k in ftype for k in ('malware', 'ransomware', 'trojan', 'virus'))):
        return 'malware_activity'
    if any((k in ftype for k in ('phishing', 'social_engineering', 'spoof'))):
        return 'phishing_campaign'
    if any((k in ftype for k in ('domain', 'dns', 'c2', 'command_control'))):
        return 'infrastructure'
    if any((k in ftype for k in ('url', 'uri', 'link'))):
        return 'url_analysis'
    if any((k in ftype for k in ('file', 'hash', 'md5', 'sha', 'sample'))):
        return 'file_intel'
    if any((k in ftype for k in ('ip', 'addr', 'asn', 'bgp'))):
        return 'network_intel'
    if any((k in ftype for k in ('leak', 'breach', 'exposed', 'credentials'))):
        return 'data_breach'
    if any((k in ftype for k in ('vuln', 'cve', 'exploit', 'patch'))):
        return 'vulnerability'
    if any((k in ftype for k in ('pattern', 'correlation', 'anomaly'))):
        return f'pattern_{source}'
    return ftype

def _count_anomalies(findings: list[dict[str, Any]]) -> int:
    """Count simple anomalies in findings."""
    count = 0
    for f in findings:
        desc = f.get('description', '').lower()
        if any((k in desc for k in ('future_timestamp', 'clock_skew', 'temporal', 'anomaly'))):
            count += 1
        if f.get('confidence', 0) < 0.3:
            count += 1
    return count

def _calc_cross_source_confidence(findings: list[dict[str, Any]], theme_source_overlap: dict[str, list[str]], campaign_hints: list[dict[str, Any]]) -> float:
    """Calculate 0.0-1.0 multi-source corroboration confidence.

    Signal: same IOC/indicator seen across multiple independent sources.
    """
    if not findings:
        return 0.0
    sources = {f['source'] for f in findings}
    source_diversity = min(len(sources) / max(len(findings), 1), 1.0)
    multi_source_themes = sum((1 for srcs in theme_source_overlap.values() if len(srcs) >= 2))
    theme_coverage = min(multi_source_themes / max(len(theme_source_overlap), 1), 1.0)
    campaign_bonus = 0.2 if campaign_hints else 0.0
    has_repeated = any((f.get('description', '') for f in findings if any((ioc in f.get('description', '').lower() for ioc in ('domain', 'ip', 'hash', 'url')))))
    ioc_bonus = 0.15 if has_repeated else 0.0
    confidence = source_diversity * 0.35 + theme_coverage * 0.3 + campaign_bonus + ioc_bonus
    return round(min(confidence, 1.0), 2)

def _get_corroborated_iocs(findings: list[dict[str, Any]], repeated_iocs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return IOCs that appear with 2+ source evidence.

    Corroborated = repeated across findings + high severity + high confidence.
    """
    if not repeated_iocs:
        return []
    corroborated: list[dict[str, Any]] = []
    for ioc in repeated_iocs[:10]:
        value = ioc.get('value', '')
        ioc_type = ioc.get('type', 'unknown')
        ioc.get('count', 1)
        matching = [f for f in findings if value.lower() in f.get('description', '').lower()]
        if len(matching) >= 2:
            avg_conf = sum((f.get('confidence', 0.5) for f in matching)) / max(len(matching), 1)
            max_sev = max((SEVERITY_WEIGHTS.get(f.get('severity', 'medium').lower(), 0.25) for f in matching))
            corroborated.append({'value': value, 'type': ioc_type, 'source_count': len(matching), 'confidence': round(avg_conf, 2), 'severity_weight': round(max_sev, 2), 'actionable': avg_conf >= 0.7 and max_sev >= 0.5})
    corroborated.sort(key=lambda x: (-x['source_count'], -x['confidence']))
    return corroborated[:8]

def _get_top_priority_pivots(findings: list[dict[str, Any]], dominant_cluster: str | None, high_risk_branch: list[dict[str, Any]], repeated_domains: list[str]) -> list[dict[str, Any]]:
    """Build bounded priority shortlist for operator.

    Max 5 pivots. Prioritizes: infra-heavy, corroborated, high-severity.
    """
    pivots: list[dict[str, Any]] = []
    if dominant_cluster:
        pivots.append({'pivot_type': 'dominant_cluster', 'description': f'Primary cluster: {dominant_cluster}', 'priority': 1})
    for f in high_risk_branch[:2]:
        if len(pivots) >= 5:
            break
        pivots.append({'pivot_type': 'high_risk_infra', 'value': _extract_primary_entity(f), 'description': f.get('description', '')[:120], 'severity': f.get('severity', 'medium'), 'priority': 2})
    for domain in repeated_domains[:2]:
        if len(pivots) >= 5:
            break
        pivots.append({'pivot_type': 'repeated_domain', 'value': domain, 'description': 'Domain seen across multiple findings', 'priority': 3})
    for f in findings:
        if len(pivots) >= 5:
            break
        if f.get('confidence', 0) >= 0.85 and f.get('severity', '').lower() in ('high', 'critical'):
            entity = _extract_primary_entity(f)
            if entity and (not any((p.get('value') == entity for p in pivots))):
                pivots.append({'pivot_type': 'high_conf_ioc', 'value': entity, 'description': f.get('description', '')[:120], 'confidence': f.get('confidence'), 'priority': 4})
    return pivots[:5]

def _calc_campaign_confidence(campaign_hints: list[dict[str, Any]], theme_source_overlap: dict[str, list[str]]) -> float:
    """Calculate 0.0-1.0 campaign cluster confidence.

    Evidence: multi_source_cluster hints + overlapping themes across sources.
    """
    if not campaign_hints:
        return 0.0
    multi_source_signals = [h for h in campaign_hints if h.get('type') == 'multi_source_cluster']
    high_conf_signals = [h for h in campaign_hints if h.get('type') == 'high_confidence_cluster']
    overlapping_themes = sum((1 for srcs in theme_source_overlap.values() if len(srcs) >= 2))
    overlap_factor = min(overlapping_themes / 3.0, 0.4)
    multi_source_score = min(len(multi_source_signals) * 0.25, 0.4)
    high_conf_score = min(len(high_conf_signals) * 0.15, 0.2)
    confidence = multi_source_score + high_conf_score + overlap_factor
    return round(min(confidence, 1.0), 2)

def _extract_primary_entity(finding: dict[str, Any]) -> str:
    """Extract primary IOC entity from finding description."""
    import re
    text = finding.get('description', '') + ' ' + finding.get('type', '')
    domain_match = re.search('\\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,}\\b', text, re.IGNORECASE)
    if domain_match:
        return domain_match.group(0)
    ip_match = re.search('\\b(?:(?:25[0-5]|2[0-4]\\d|[01]?\\d{1,2})\\.){3}(?:25[0-5]|2[0-4]\\d|[01]?\\d{1,2})\\b', text)
    if ip_match:
        return ip_match.group(0)
    hash_match = re.search('\\b[a-f0-9]{32,}\\b', text, re.IGNORECASE)
    if hash_match:
        return hash_match.group(0)[:32]
    return ''

def _get_what_matters_first(verdict: str, dominant_cluster: str | None, high_risk_branch: list[dict[str, Any]], corroborated_iocs: list[dict[str, Any]]) -> str:
    """Return single primary action/takeaway for operator."""
    if verdict == 'HIGH_RISK':
        if dominant_cluster:
            return f"Investigate cluster '{dominant_cluster}' — highest-risk theme"
        if high_risk_branch:
            return f'Pivot on {len(high_risk_branch)} critical/high findings with infra signals'
        if corroborated_iocs:
            top = corroborated_iocs[0]
            return f"Corroborated IOC: {top.get('type', 'ioc')}={top.get('value', '?')} (sources={top.get('source_count', 1)})"
        return 'HIGH_RISK verdict — review all high-severity findings'
    elif verdict == 'SUSPICIOUS':
        if dominant_cluster:
            return f"Monitor cluster '{dominant_cluster}' for escalation"
        return 'Anomalies detected — verify with additional sources'
    return 'No immediate action required'

def _build_operator_shortlist(dominant_cluster: str | None, high_risk_branch: list[dict[str, Any]], corroborated_iocs: list[dict[str, Any]], risk_score: float) -> list[dict[str, Any]]:
    """Build max-3 bounded prioritised shortlist for scheduler/export.

    Returns items with: action, target, rationale (scheduler-consumable shape).

    Scheduler transformation: action=query, target=rationale[:80], rationale=pivot_type
    """
    shortlist: list[dict[str, Any]] = []
    if dominant_cluster:
        shortlist.append({'action': dominant_cluster, 'target': 'highest-risk theme with most critical findings', 'rationale': 'dominant_cluster'})
    for ioc in corroborated_iocs[:2]:
        if len(shortlist) >= 3:
            break
        shortlist.append({'action': ioc.get('value', ''), 'target': f"{ioc.get('type')} seen across {ioc.get('source_count', 1)} sources", 'rationale': 'corroborated_ioc'})
    for f in high_risk_branch[:2]:
        if len(shortlist) >= 3:
            break
        shortlist.append({'action': _extract_primary_entity(f), 'target': f['severity'].upper() + ' + infra hint', 'rationale': 'high_risk_infra'})
    return shortlist[:3]

def _build_confidence_note(risk_score: float, cross_source_confidence: float, campaign_confidence: float) -> str:
    """Human-readable confidence explanation."""
    if risk_score >= 0.7:
        base = 'HIGH RISK verdict'
    elif risk_score >= 0.3:
        base = 'SUSPICIOUS verdict'
    else:
        base = 'CLEAN verdict'
    if cross_source_confidence >= 0.6:
        corroboration = 'strong multi-source corroboration'
    elif cross_source_confidence >= 0.3:
        corroboration = 'moderate cross-source agreement'
    else:
        corroboration = 'limited source corroboration'
    if campaign_confidence >= 0.5:
        campaign = 'campaign cluster likely'
    elif campaign_confidence >= 0.2:
        campaign = 'possible campaign cluster'
    else:
        campaign = 'no campaign signals'
    return f'{base} | {corroboration} | {campaign}'

def _classify_signal_quality(risk_score: float, cross_source_confidence: float, campaign_confidence: float, corroborated_iocs: list[dict[str, Any]]) -> str:
    """Classify signal as strong/mixed/weak for scheduler filtering."""
    strong_indicators = risk_score >= 0.6 and cross_source_confidence >= 0.5 and (len(corroborated_iocs) >= 2)
    weak_indicators = risk_score < 0.3 and cross_source_confidence < 0.2 and (not corroborated_iocs)
    if strong_indicators:
        return 'strong'
    elif weak_indicators:
        return 'weak'
    return 'mixed'

def create_workflow_orchestrator(orchestrator: Any, config: IntelligenceConfig | None=None) -> WorkflowOrchestrator:
    """Create a configured WorkflowOrchestrator instance.

    Args:
        orchestrator: Main orchestrator instance
        config: Optional intelligence configuration

    Returns:
        Configured WorkflowOrchestrator instance
    """
    return WorkflowOrchestrator(orchestrator, config)