"""Workflow Orchestrator for OSINT intelligence analysis.

Coordinates multiple analysis modules, correlates results, detects anomalies,
and generates comprehensive reports with risk assessment.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Module timeout in seconds
MODULE_TIMEOUT = 60


@dataclass
class Finding:
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
    modules: List[str] = field(default_factory=list)


@dataclass
class CorrelationReport:
    """Report of cross-module correlations.

    Attributes:
        cross_module_findings: List of findings from multiple modules
        risk_score: Calculated risk score (0.0-1.0)
        attribution: Attribution data (e.g., threat actor, source)
    """
    cross_module_findings: List[Finding] = field(default_factory=list)
    risk_score: float = 0.0
    attribution: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Anomaly:
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
    affected_modules: List[str] = field(default_factory=list)


@dataclass
class SharedContext:
    """Shared context passed between workflow modules.

    Attributes:
        input_data: Original input data
        intermediate_results: Results from completed modules
        module_status: Status tracking for each module
        resource_usage: Resource usage statistics
    """
    input_data: Any = None
    intermediate_results: Dict[str, Any] = field(default_factory=dict)
    module_status: Dict[str, str] = field(default_factory=dict)
    resource_usage: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ComprehensiveReport:
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
    input_summary: Dict[str, Any] = field(default_factory=dict)
    module_results: Dict[str, Any] = field(default_factory=dict)
    correlations: CorrelationReport = field(default_factory=lambda: CorrelationReport())
    anomalies: List[Anomaly] = field(default_factory=list)
    verdict: str = "CLEAN"
    confidence: float = 0.0
    recommendations: List[str] = field(default_factory=list)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    export_data: Dict[str, Any] = field(default_factory=dict)

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
                return {
                    "cross_module_findings": [f.__dict__ for f in obj.cross_module_findings],
                    "risk_score": obj.risk_score,
                    "attribution": obj.attribution
                }
            if isinstance(obj, ComprehensiveReport):
                return {
                    "input_summary": obj.input_summary,
                    "module_results": obj.module_results,
                    "correlations": serialize(obj.correlations),
                    "anomalies": [a.__dict__ for a in obj.anomalies],
                    "verdict": obj.verdict,
                    "confidence": obj.confidence,
                    "recommendations": obj.recommendations,
                    "timeline": obj.timeline,
                    "export_data": obj.export_data
                }
            return obj

        return json.dumps(serialize(self), indent=2, default=serialize)

    def to_markdown(self) -> str:
        """Export report as Markdown string.

        Returns:
            Markdown formatted report
        """
        lines = [
            "# Comprehensive Analysis Report",
            "",
            f"**Verdict:** {self.verdict}",
            f"**Confidence:** {self.confidence:.2%}",
            f"**Generated:** {datetime.now().isoformat()}",
            "",
            "## Input Summary",
            ""
        ]

        for key, value in self.input_summary.items():
            lines.append(f"- **{key}:** {value}")

        lines.extend(["", "## Module Results", ""])
        for module, result in self.module_results.items():
            lines.append(f"### {module}")
            lines.append(f"```json\n{json.dumps(result, indent=2, default=str)}\n```")
            lines.append("")

        lines.extend(["", "## Correlations", ""])
        lines.append(f"**Risk Score:** {self.correlations.risk_score:.2%}")
        lines.append("")
        for finding in self.correlations.cross_module_findings:
            lines.append(f"- **{finding.finding_type}** ({finding.severity}): {finding.description}")

        lines.extend(["", "## Anomalies", ""])
        for anomaly in self.anomalies:
            lines.append(f"- **{anomaly.anomaly_type}** ({anomaly.severity}): {anomaly.description}")

        lines.extend(["", "## Recommendations", ""])
        for i, rec in enumerate(self.recommendations, 1):
            lines.append(f"{i}. {rec}")

        return "\n".join(lines)

    def to_html(self) -> str:
        """Export report as HTML string.

        Returns:
            HTML formatted report
        """
        verdict_class = {
            "CLEAN": "success",
            "SUSPICIOUS": "warning",
            "HIGH_RISK": "danger"
        }.get(self.verdict, "info")

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Analysis Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; }}
        .header {{ border-bottom: 2px solid #ddd; padding-bottom: 20px; margin-bottom: 30px; }}
        .verdict {{ display: inline-block; padding: 10px 20px; border-radius: 4px; font-weight: bold; }}
        .verdict.success {{ background: #d4edda; color: #155724; }}
        .verdict.warning {{ background: #fff3cd; color: #856404; }}
        .verdict.danger {{ background: #f8d7da; color: #721c24; }}
        .section {{ margin: 30px 0; }}
        .section h2 {{ color: #333; border-bottom: 1px solid #eee; padding-bottom: 10px; }}
        .finding {{ padding: 10px; margin: 10px 0; background: #f8f9fa; border-left: 4px solid #007bff; }}
        .anomaly {{ padding: 10px; margin: 10px 0; background: #fff3cd; border-left: 4px solid #ffc107; }}
        .risk-score {{ font-size: 24px; font-weight: bold; color: {'#dc3545' if self.correlations.risk_score > 0.7 else '#ffc107' if self.correlations.risk_score > 0.3 else '#28a745'}; }}
        pre {{ background: #f4f4f4; padding: 15px; border-radius: 4px; overflow-x: auto; }}
        .recommendation {{ padding: 10px; margin: 5px 0; background: #e7f3ff; border-radius: 4px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Comprehensive Analysis Report</h1>
            <span class="verdict {verdict_class}">{self.verdict}</span>
            <p><strong>Confidence:</strong> {self.confidence:.2%}</p>
            <p><strong>Generated:</strong> {datetime.now().isoformat()}</p>
        </div>

        <div class="section">
            <h2>Risk Assessment</h2>
            <div class="risk-score">Risk Score: {self.correlations.risk_score:.2%}</div>
        </div>

        <div class="section">
            <h2>Input Summary</h2>
            <ul>
"""
        for key, value in self.input_summary.items():
            html += f"                <li><strong>{key}:</strong> {value}</li>\n"

        html += """            </ul>
        </div>

        <div class="section">
            <h2>Correlations</h2>
"""
        for finding in self.correlations.cross_module_findings:
            html += f"""            <div class="finding">
                <strong>{finding.finding_type}</strong> ({finding.severity})
                <p>{finding.description}</p>
                <small>Modules: {', '.join(finding.modules)}</small>
            </div>
"""

        html += """        </div>

        <div class="section">
            <h2>Anomalies</h2>
"""
        for anomaly in self.anomalies:
            html += f"""            <div class="anomaly">
                <strong>{anomaly.anomaly_type}</strong> ({anomaly.severity})
                <p>{anomaly.description}</p>
                <small>Affected: {', '.join(anomaly.affected_modules)}</small>
            </div>
"""

        html += """        </div>

        <div class="section">
            <h2>Recommendations</h2>
"""
        for rec in self.recommendations:
            html += f'            <div class="recommendation">{rec}</div>\n'

        html += """        </div>

        <div class="section">
            <h2>Module Results</h2>
"""
        for module, result in self.module_results.items():
            html += f"""            <h3>{module}</h3>
            <pre>{json.dumps(result, indent=2, default=str)}</pre>
"""

        html += """        </div>
    </div>
</body>
</html>"""

        return html


@dataclass
class WorkflowPlan:
    """Plan for workflow execution.

    Attributes:
        modules: List of module names to execute
        execution_mode: "sequential" or "parallel"
        parallel_groups: Optional grouping for parallel execution
    """
    modules: List[str] = field(default_factory=list)
    execution_mode: str = "sequential"
    parallel_groups: Optional[List[List[str]]] = None


@dataclass
class IntelligenceConfig:
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
    risk_thresholds: Dict[str, float] = field(default_factory=lambda: {
        "clean": 0.3,
        "suspicious": 0.7
    })


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

    # High-risk correlation patterns with risk score increments
    HIGH_RISK_PATTERNS = {
        ("scrubbed_metadata", "steganography_detected"): 0.5,
        ("dns_tunneling", "encoded_payload"): 0.4,
        ("zero_width_unicode", "base64_hidden"): 0.3,
        ("future_timestamp", "gps_mismatch"): 0.2,
    }

    def __init__(
        self,
        orchestrator: Any,
        config: Optional[IntelligenceConfig] = None
    ):
        """Initialize workflow orchestrator.

        Args:
            orchestrator: Main orchestrator instance for module access
            config: Optional intelligence configuration
        """
        self.orchestrator = orchestrator
        self.config = config or IntelligenceConfig()
        self._module_registry: Dict[str, Any] = {}
        self._execution_timeline: List[Dict[str, Any]] = []

    def _add_timeline_event(self, event_type: str, details: Dict[str, Any]) -> None:
        """Add event to execution timeline.

        Args:
            event_type: Type of event
            details: Event details
        """
        self._execution_timeline.append({
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "details": details
        })

    async def execute_workflow(
        self,
        workflow: WorkflowPlan,
        input_data: Any
    ) -> ComprehensiveReport:
        """Execute a workflow plan.

        Args:
            workflow: Workflow plan with module configuration
            input_data: Input data for analysis

        Returns:
            Comprehensive analysis report
        """
        start_time = time.time()
        self._execution_timeline = []

        self._add_timeline_event("workflow_start", {
            "modules": workflow.modules,
            "mode": workflow.execution_mode
        })

        # Create shared context
        context = SharedContext(
            input_data=input_data,
            intermediate_results={},
            module_status={m: "pending" for m in workflow.modules},
            resource_usage={}
        )

        try:
            # Execute modules based on mode
            if workflow.execution_mode == "parallel" and workflow.parallel_groups:
                results = await self._execute_parallel(
                    workflow.parallel_groups, input_data, context
                )
            else:
                results = await self._execute_sequential(
                    workflow.modules, input_data, context
                )

            self._add_timeline_event("modules_complete", {
                "completed": len(results),
                "failed": len(workflow.modules) - len(results)
            })

            # Correlate results
            correlations = CorrelationReport()
            if self.config.enable_correlation:
                correlations = self._correlate_results(results)
                self._add_timeline_event("correlation_complete", {
                    "findings": len(correlations.cross_module_findings),
                    "risk_score": correlations.risk_score
                })

            # Detect anomalies
            anomalies: List[Anomaly] = []
            if self.config.enable_anomaly_detection:
                anomalies = self._detect_anomalies(results)
                self._add_timeline_event("anomaly_detection_complete", {
                    "anomalies": len(anomalies)
                })

            # Generate report
            report = self._generate_report(
                results, correlations, anomalies, context
            )

            duration = time.time() - start_time
            self._add_timeline_event("workflow_complete", {
                "duration_seconds": duration,
                "verdict": report.verdict
            })
            report.timeline = self._execution_timeline

            return report

        except Exception as e:
            logger.error(f"Workflow execution failed: {e}")
            self._add_timeline_event("workflow_error", {"error": str(e)})
            raise

    async def _execute_sequential(
        self,
        modules: List[str],
        input_data: Any,
        context: SharedContext
    ) -> Dict[str, Any]:
        """Execute modules sequentially.

        Args:
            modules: List of module names
            input_data: Input data
            context: Shared execution context

        Returns:
            Dictionary of module results
        """
        results: Dict[str, Any] = {}

        for module in modules:
            try:
                result = await self._execute_module(module, input_data, context)
                if result is not None:
                    results[module] = result
                    context.intermediate_results[module] = result
            except Exception as e:
                logger.error(f"Module {module} failed: {e}")
                context.module_status[module] = "failed"

        return results

    async def _execute_parallel(
        self,
        module_groups: List[List[str]],
        input_data: Any,
        context: SharedContext
    ) -> Dict[str, Any]:
        """Execute modules in parallel groups.

        Args:
            module_groups: Groups of modules to execute in parallel
            input_data: Input data
            context: Shared execution context

        Returns:
            Dictionary of module results
        """
        results: Dict[str, Any] = {}

        for group in module_groups:
            # Execute group in parallel with timeout
            tasks = [
                asyncio.wait_for(
                    self._execute_module(module, input_data, context),
                    timeout=self.config.module_timeout
                )
                for module in group
            ]

            group_results = await asyncio.gather(*tasks, return_exceptions=True)

            for module, result in zip(group, group_results):
                if isinstance(result, Exception):
                    logger.error(f"Module {module} failed: {result}")
                    context.module_status[module] = "failed"
                elif result is not None:
                    results[module] = result
                    context.intermediate_results[module] = result

        return results

    async def _execute_module(
        self,
        module: str,
        input_data: Any,
        context: SharedContext
    ) -> Any:
        """Execute a single module.

        Args:
            module: Module name
            input_data: Input data
            context: Shared execution context

        Returns:
            Module execution result
        """
        context.module_status[module] = "running"
        module_start = time.time()

        try:
            # Get module from orchestrator or registry
            module_instance = self._get_module_instance(module)

            if module_instance is None:
                logger.warning(f"Module {module} not found")
                context.module_status[module] = "not_found"
                return None

            # Execute with timeout
            if inspect.iscoroutinefunction(module_instance):
                result = await asyncio.wait_for(
                    module_instance(input_data),
                    timeout=self.config.module_timeout
                )
            elif hasattr(module_instance, 'analyze'):
                if inspect.iscoroutinefunction(module_instance.analyze):
                    result = await asyncio.wait_for(
                        module_instance.analyze(input_data),
                        timeout=self.config.module_timeout
                    )
                else:
                    result = module_instance.analyze(input_data)
            elif hasattr(module_instance, 'process'):
                if inspect.iscoroutinefunction(module_instance.process):
                    result = await asyncio.wait_for(
                        module_instance.process(input_data),
                        timeout=self.config.module_timeout
                    )
                else:
                    result = module_instance.process(input_data)
            else:
                result = {"error": f"No valid method found for {module}"}

            duration = time.time() - module_start
            context.module_status[module] = "completed"
            context.resource_usage[module] = {"duration_seconds": duration}

            self._add_timeline_event("module_complete", {
                "module": module,
                "duration_seconds": duration
            })

            return result

        except asyncio.TimeoutError:
            logger.error(f"Module {module} timed out after {self.config.module_timeout}s")
            context.module_status[module] = "timeout"
            return {"error": "timeout", "module": module}
        except Exception as e:
            logger.error(f"Module {module} error: {e}")
            context.module_status[module] = "error"
            return {"error": str(e), "module": module}

    def _get_module_instance(self, module: str) -> Any:
        """Get module instance from registry or orchestrator.

        Args:
            module: Module name

        Returns:
            Module instance or None
        """
        # Check local registry first
        if module in self._module_registry:
            return self._module_registry[module]

        # Try to get from orchestrator (defensive with try/except to avoid ANY exception)
        try:
            get_module = getattr(self.orchestrator, 'get_module', None)
            if get_module is not None:
                return get_module(module)
        except Exception:
            pass
        try:
            if hasattr(self.orchestrator, module):
                return getattr(self.orchestrator, module)
        except Exception:
            pass

        return None

    def register_module(self, name: str, instance: Any) -> None:
        """Register a module instance.

        Args:
            name: Module name
            instance: Module instance
        """
        self._module_registry[name] = instance

    def _correlate_results(self, results: Dict[str, Any]) -> CorrelationReport:
        """Correlate results across modules.

        Args:
            results: Dictionary of module results

        Returns:
            Correlation report with findings and risk score
        """
        findings: List[Finding] = []
        risk_score = 0.0
        attribution: Dict[str, Any] = {}

        # Check for high-risk correlation patterns
        detected_patterns = set()
        for module, result in results.items():
            if isinstance(result, dict):
                for key in result.keys():
                    detected_patterns.add((module, key))
                if result.get("detected"):
                    detected_patterns.add((module, result.get("type", "unknown")))

        # Apply high-risk patterns
        for pattern, risk_increment in self.HIGH_RISK_PATTERNS.items():
            if pattern in detected_patterns or self._check_pattern(results, pattern):
                risk_score += risk_increment
                findings.append(Finding(
                    finding_type="high_risk_correlation",
                    description=f"Detected correlation: {pattern[0]} + {pattern[1]}",
                    severity="high" if risk_increment >= 0.4 else "medium",
                    confidence=0.8,
                    modules=list(results.keys())
                ))

        # Cross-reference indicators
        indicators = self._extract_indicators(results)
        if len(indicators) > 1:
            # Multiple indicators increase risk
            risk_score += min(0.1 * (len(indicators) - 1), 0.3)
            findings.append(Finding(
                finding_type="multiple_indicators",
                description=f"Multiple suspicious indicators detected: {len(indicators)}",
                severity="medium",
                confidence=0.7,
                modules=list(results.keys())
            ))

        # Check for attribution clues
        attribution = self._extract_attribution(results)

        # Cap risk score at 1.0
        risk_score = min(risk_score, 1.0)

        return CorrelationReport(
            cross_module_findings=findings,
            risk_score=risk_score,
            attribution=attribution
        )

    def _check_pattern(
        self,
        results: Dict[str, Any],
        pattern: Tuple[str, str]
    ) -> bool:
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
            return (
                result.get("detected") or
                result.get("type") == indicator or
                indicator in str(result).lower()
            )
        return False

    def _extract_indicators(self, results: Dict[str, Any]) -> List[str]:
        """Extract suspicious indicators from results.

        Args:
            results: Module results

        Returns:
            List of indicator strings
        """
        indicators = []
        for module, result in results.items():
            if isinstance(result, dict):
                if result.get("suspicious") or result.get("detected"):
                    indicators.append(module)
                if result.get("indicators"):
                    indicators.extend(result["indicators"])
        return indicators

    def _extract_attribution(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Extract attribution information from results.

        Args:
            results: Module results

        Returns:
            Attribution dictionary
        """
        attribution = {}
        for module, result in results.items():
            if isinstance(result, dict):
                if result.get("attribution"):
                    attribution[module] = result["attribution"]
                if result.get("source"):
                    attribution["source"] = result["source"]
        return attribution

    def _detect_anomalies(self, results: Dict[str, Any]) -> List[Anomaly]:
        """Detect anomalies in module results.

        Args:
            results: Module results

        Returns:
            List of detected anomalies
        """
        anomalies: List[Anomaly] = []

        # Check for module failures
        for module, result in results.items():
            if isinstance(result, dict) and result.get("error"):
                anomalies.append(Anomaly(
                    anomaly_type="module_failure",
                    severity="medium",
                    description=f"Module {module} failed: {result['error']}",
                    affected_modules=[module]
                ))

        # Check for inconsistent results
        if len(results) > 1:
            confidence_values = []
            for result in results.values():
                if isinstance(result, dict) and result.get("confidence"):
                    confidence_values.append(result["confidence"])

            if confidence_values:
                import statistics
                if len(confidence_values) > 1:
                    variance = statistics.variance(confidence_values)
                    if variance > 0.2:
                        anomalies.append(Anomaly(
                            anomaly_type="high_confidence_variance",
                            severity="low",
                            description=f"High variance in module confidence: {variance:.2f}",
                            affected_modules=list(results.keys())
                        ))

        # Check for data inconsistencies
        timestamps = []
        for module, result in results.items():
            if isinstance(result, dict) and result.get("timestamp"):
                try:
                    ts = datetime.fromisoformat(result["timestamp"])
                    timestamps.append((module, ts))
                except (ValueError, TypeError):
                    pass

        if len(timestamps) > 1:
            # Check for future timestamps
            now = datetime.now()
            for module, ts in timestamps:
                if ts > now:
                    anomalies.append(Anomaly(
                        anomaly_type="future_timestamp",
                        severity="high",
                        description=f"Future timestamp detected in {module}",
                        affected_modules=[module]
                    ))

        return anomalies

    def _generate_report(
        self,
        results: Dict[str, Any],
        correlations: CorrelationReport,
        anomalies: List[Anomaly],
        context: SharedContext
    ) -> ComprehensiveReport:
        """Generate comprehensive report.

        Args:
            results: Module results
            correlations: Correlation report
            anomalies: Detected anomalies
            context: Shared execution context

        Returns:
            Comprehensive analysis report
        """
        # Generate input summary
        input_summary = {
            "type": type(context.input_data).__name__,
            "size": len(str(context.input_data)) if context.input_data else 0,
            "modules_executed": len(results),
            "execution_mode": "parallel" if context.module_status else "sequential"
        }

        # Calculate confidence
        confidence_values = []
        for result in results.values():
            if isinstance(result, dict) and result.get("confidence"):
                confidence_values.append(result["confidence"])

        overall_confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values else 0.5
        )

        # Generate recommendations
        recommendations = self._generate_recommendations(
            results, correlations, anomalies
        )

        # Determine verdict
        verdict = self._get_verdict(correlations.risk_score)

        # Prepare export data
        export_data = {
            "version": "1.0",
            "generated_at": datetime.now().isoformat(),
            "total_modules": len(context.module_status),
            "successful_modules": len(results),
            "risk_score": correlations.risk_score
        }

        return ComprehensiveReport(
            input_summary=input_summary,
            module_results=results,
            correlations=correlations,
            anomalies=anomalies,
            verdict=verdict,
            confidence=overall_confidence,
            recommendations=recommendations,
            timeline=self._execution_timeline,
            export_data=export_data
        )

    def _generate_recommendations(
        self,
        results: Dict[str, Any],
        correlations: CorrelationReport,
        anomalies: List[Anomaly]
    ) -> List[str]:
        """Generate actionable recommendations.

        Args:
            results: Module results
            correlations: Correlation report
            anomalies: Detected anomalies

        Returns:
            List of recommendation strings
        """
        recommendations = []

        # Risk-based recommendations
        if correlations.risk_score >= 0.7:
            recommendations.append(
                "HIGH RISK: Immediate investigation recommended. "
                "Multiple suspicious indicators detected."
            )
        elif correlations.risk_score >= 0.3:
            recommendations.append(
                "SUSPICIOUS: Further analysis recommended. "
                "Some indicators warrant closer examination."
            )

        # Anomaly-based recommendations
        for anomaly in anomalies:
            if anomaly.anomaly_type == "future_timestamp":
                recommendations.append(
                    "Verify system clock and timestamp sources. "
                    "Future timestamps may indicate manipulation."
                )
            elif anomaly.anomaly_type == "module_failure":
                recommendations.append(
                    f"Re-run failed module: {anomaly.affected_modules[0]}. "
                    "Results may be incomplete."
                )

        # Module-specific recommendations
        for module, result in results.items():
            if isinstance(result, dict):
                if result.get("recommendations"):
                    recommendations.extend(result["recommendations"])
                if result.get("detected") and module == "steganography":
                    recommendations.append(
                        "Extract and analyze hidden content using specialized tools."
                    )
                if result.get("detected") and module == "metadata":
                    recommendations.append(
                        "Review metadata for OPSEC violations and attribution clues."
                    )

        # Remove duplicates while preserving order
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
        clean_threshold = self.config.risk_thresholds.get("clean", 0.3)
        suspicious_threshold = self.config.risk_thresholds.get("suspicious", 0.7)

        if risk_score < clean_threshold:
            return "CLEAN"
        elif risk_score < suspicious_threshold:
            return "SUSPICIOUS"
        else:
            return "HIGH_RISK"


# =============================================================================
# STANDALONE POST-FINDINGS CORRELATION SEAM
# Bounded, fail-soft, no dependencies, M1 8GB safe
# =============================================================================

HIGH_RISK_PATTERNS: Dict[Tuple[str, str], float] = {
    ("scrubbed_metadata", "steganography_detected"): 0.5,
    ("dns_tunneling", "encoded_payload"): 0.4,
    ("zero_width_unicode", "base64_hidden"): 0.3,
    ("future_timestamp", "gps_mismatch"): 0.2,
}

SEVERITY_WEIGHTS = {"critical": 1.0, "high": 0.75, "medium": 0.5, "low": 0.25}


@dataclass
class CorrelationResult:
    """Lightweight correlation result from findings analysis.

    Attributes:
        themes: Grouped findings by correlation theme
        risk_score: Overall risk score (0.0-1.0)
        risk_buckets: Findings bucketed by risk level
        top_themes: Top 5 most significant themes sorted by weight
        anomaly_count: Number of detected anomalies
        verdict: Risk verdict string

        # --- NEW: actionable condensation ---
        source_themes: Dict[str, List[str]]           # source -> list of theme keys
        top_entities: List[Dict[str, Any]]            # extracted IOCs (domain/ip/hash/url)
        repeated_domains: List[str]                   # domains seen across >1 finding
        repeated_iocs: List[Dict[str, Any]]          # IOCs appearing >1 time
        dominant_cluster: Optional[str]               # theme with most high-severity findings
        high_risk_branch: List[Dict[str, Any]]        # critical/high findings with infra hints
        theme_source_overlap: Dict[str, List[str]]   # theme -> sources contributing
        campaign_hints: List[Dict[str, Any]]          # findings suggesting same campaign
        coupling_pairs: List[Tuple[str, str]]          # (entity, related_entity) pairs
        so_what: str                                   # one-liner operator takeaway

        # --- SECOND-ORDER CONDENSATION (sprint delta) ---
        cross_source_confidence: float = 0.0       # 0.0-1.0: multi-source corroboration score
        corroborated_iocs: List[Dict[str, Any]] = field(default_factory=list)  # IOCs with 2+ source evidence
        top_priority_pivots: List[Dict[str, Any]] = field(default_factory=list)  # bounded action shortlist
        campaign_confidence: float = 0.0            # 0.0-1.0: campaign cluster confidence
    """
    themes: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    risk_score: float = 0.0
    risk_buckets: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    top_themes: List[Tuple[str, float]] = field(default_factory=list)
    anomaly_count: int = 0
    verdict: str = "CLEAN"
    # NEW fields (with defaults so existing callers don't break)
    source_themes: Dict[str, List[str]] = field(default_factory=dict)
    top_entities: List[Dict[str, Any]] = field(default_factory=list)
    repeated_domains: List[str] = field(default_factory=list)
    repeated_iocs: List[Dict[str, Any]] = field(default_factory=list)
    dominant_cluster: Optional[str] = None
    high_risk_branch: List[Dict[str, Any]] = field(default_factory=list)
    theme_source_overlap: Dict[str, List[str]] = field(default_factory=dict)
    campaign_hints: List[Dict[str, Any]] = field(default_factory=list)
    coupling_pairs: List[Tuple[str, str]] = field(default_factory=list)
    so_what: str = ""
    # --- SECOND-ORDER CONDENSATION (sprint delta) ---
    cross_source_confidence: float = 0.0       # 0.0-1.0: multi-source corroboration score
    corroborated_iocs: List[Dict[str, Any]] = field(default_factory=list)  # IOCs with 2+ source evidence
    top_priority_pivots: List[Dict[str, Any]] = field(default_factory=list)  # bounded action shortlist
    campaign_confidence: float = 0.0           # 0.0-1.0: campaign cluster confidence

    # --- SECOND-ORDER: OPERATOR ACTIONABLE SHORTLIST (sprint delta) ---
    what_matters_first: str = ""                # single primary action/takeaway for operator
    operator_shortlist: List[Dict[str, Any]] = field(default_factory=list)  # max 3 bounded prioritised items
    confidence_note: str = ""                  # human-readable confidence explanation
    signal_quality: str = "weak"               # "strong" | "mixed" | "weak" — scheduler filter


def correlate_findings(
    findings: List[Dict[str, Any]],
    *,
    risk_thresholds: Optional[Dict[str, float]] = None,
    max_themes: int = 10,
) -> CorrelationResult:
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

    thresholds = risk_thresholds or {"clean": 0.3, "suspicious": 0.7}

    # --- Normalize findings to canonical form ---
    normalized: List[Dict[str, Any]] = []
    for f in findings:
        nf: Dict[str, Any] = {
            "type": f.get("type") or f.get("finding_type") or f.get("indicator_type", "unknown"),
            "severity": f.get("severity", "medium"),
            "confidence": float(f.get("confidence", 0.5)),
            "description": f.get("description") or f.get("description_text", ""),
            "source": f.get("source") or f.get("module") or f.get("tag") or f.get("tags", ["unknown"]),
        }
        if isinstance(nf["source"], list):
            nf["source"] = nf["source"][0] if nf["source"] else "unknown"
        normalized.append(nf)

    # --- Risk scoring ---
    risk_score = 0.0
    for f in normalized:
        severity = f["severity"].lower()
        weight = SEVERITY_WEIGHTS.get(severity, 0.25)
        risk_score += weight * f["confidence"]
    risk_score = min(risk_score / max(len(normalized), 1), 1.0)

    # --- Theme grouping ---
    themes: Dict[str, List[Dict[str, Any]]] = {}
    for f in normalized:
        theme_key = _derive_theme_key(f)
        if theme_key not in themes:
            themes[theme_key] = []
        themes[theme_key].append(f)

    # --- Theme weights ---
    theme_weights: Dict[str, float] = {}
    for theme, theme_findings in themes.items():
        weights = [SEVERITY_WEIGHTS.get(x["severity"].lower(), 0.25) * x["confidence"]
                   for x in theme_findings]
        theme_weights[theme] = sum(weights) / max(len(weights), 1)

    # --- Risk buckets ---
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "critical": [], "high": [], "medium": [], "low": []
    }
    for f in normalized:
        sev = f["severity"].lower()
        if sev in buckets:
            buckets[sev].append(f)

    # --- Anomaly detection ---
    anomaly_count = _count_anomalies(normalized)

    # --- Top themes ---
    sorted_themes = sorted(theme_weights.items(), key=lambda x: -x[1])
    top_themes = sorted_themes[:max_themes]

    # --- Verdict ---
    verdict = "CLEAN"
    if risk_score >= thresholds.get("suspicious", 0.7):
        verdict = "HIGH_RISK"
    elif risk_score >= thresholds.get("clean", 0.3):
        verdict = "SUSPICIOUS"

    # --- Source -> themes mapping ---
    source_themes: Dict[str, List[str]] = {}
    for f in normalized:
        src = f["source"]
        tk = _derive_theme_key(f)
        if src not in source_themes:
            source_themes[src] = []
        if tk not in source_themes[src]:
            source_themes[src].append(tk)

    # --- IOC / entity extraction ---
    all_entities, domain_counts, ioc_counts = _extract_entities(normalized)
    top_entities = sorted(all_entities,
                          key=lambda x: x.get("_weight", 0),
                          reverse=True)[:20]

    # --- Repeated domains (seen >1 across findings) ---
    repeated_domains = [d for d, cnt in domain_counts.items() if cnt > 1]

    # --- Repeated IOCs ---
    repeated_iocs = [
        {"value": v, "type": t, "count": c}
        for (v, t), c in ioc_counts.items() if c > 1
    ]

    # --- Dominant cluster: theme with most critical/high findings ---
    dominant_cluster = None
    cluster_scores: Dict[str, float] = {}
    for theme, fndgs in themes.items():
        score = sum(
            SEVERITY_WEIGHTS.get(x["severity"].lower(), 0.25)
            for x in fndgs if x["severity"].lower() in ("critical", "high")
        )
        if score > 0:
            cluster_scores[theme] = score
    if cluster_scores:
        dominant_cluster = max(cluster_scores, key=lambda k: cluster_scores.get(k, 0.0))

    # --- High-risk branch: critical/high + infra hints ---
    high_risk_branch = [
        f for f in normalized
        if f["severity"].lower() in ("critical", "high")
        and _has_infra_hints(f)
    ]

    # --- Theme -> sources overlap ---
    theme_source_overlap: Dict[str, List[str]] = {}
    for theme, fndgs in themes.items():
        srcs = list({x["source"] for x in fndgs})
        theme_source_overlap[theme] = srcs

    # --- Campaign hints: findings sharing same type + source cluster ---
    campaign_hints = _find_campaign_hints(normalized, themes)

    # --- Coupling pairs: entities that appear together ---
    coupling_pairs = _find_coupling_pairs(all_entities)

    # --- Operator so_what ---
    so_what = _build_so_what(
        verdict, risk_score, top_themes, dominant_cluster,
        len(high_risk_branch), anomaly_count, repeated_domains
    )

    # --- Second-order condensation (compute before return to avoid shadowing) ---
    _cross_src_conf = _calc_cross_source_confidence(
        normalized, theme_source_overlap, campaign_hints
    )
    _corr_iocs = _get_corroborated_iocs(normalized, repeated_iocs)
    _top_pivots = _get_top_priority_pivots(
        normalized, dominant_cluster, high_risk_branch, repeated_domains
    )
    _camp_conf = _calc_campaign_confidence(campaign_hints, theme_source_overlap)

    return CorrelationResult(
        themes=themes,
        risk_score=risk_score,
        risk_buckets=buckets,
        top_themes=top_themes,
        anomaly_count=anomaly_count,
        verdict=verdict,
        # NEW
        source_themes=source_themes,
        top_entities=top_entities,
        repeated_domains=repeated_domains,
        repeated_iocs=repeated_iocs,
        dominant_cluster=dominant_cluster,
        high_risk_branch=high_risk_branch,
        theme_source_overlap=theme_source_overlap,
        campaign_hints=campaign_hints,
        coupling_pairs=coupling_pairs,
        so_what=so_what,
        # Second-order condensation
        cross_source_confidence=_cross_src_conf,
        corroborated_iocs=_corr_iocs,
        top_priority_pivots=_top_pivots,
        campaign_confidence=_camp_conf,
        # Operator actionable shortlist
        what_matters_first=_get_what_matters_first(verdict, dominant_cluster, high_risk_branch, _corr_iocs),
        operator_shortlist=_build_operator_shortlist(dominant_cluster, high_risk_branch, _corr_iocs, risk_score),
        confidence_note=_build_confidence_note(risk_score, _cross_src_conf, _camp_conf),
        signal_quality=_classify_signal_quality(risk_score, _cross_src_conf, _camp_conf, _corr_iocs),
    )


def _extract_entities(
    findings: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[Tuple[str, str], int]]:
    """Extract IOCs (domains, IPs, hashes, URLs) from findings descriptions.

    Returns:
        (entities, domain_counts, ioc_counts)
        domain_counts: domain -> count across findings
        ioc_counts: (value, type) -> count across findings
    """
    entities: List[Dict[str, Any]] = []
    domain_counts: Dict[str, int] = {}
    ioc_counts: Dict[Tuple[str, str], int] = {}

    import re

    DOMAIN_RE = re.compile(
        r'\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b',
        re.IGNORECASE
    )
    IPV4_RE = re.compile(
        r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d{1,2})\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d{1,2})\b'
    )
    HASH_RE = re.compile(
        r'\b(?:[a-f0-9]{32}|[a-f0-9]{40}|[a-f0-9]{64})\b',
        re.IGNORECASE
    )
    URL_RE = re.compile(
        r'https?://[^\s<>"{}|\\^`\[\]]+',
        re.IGNORECASE
    )

    for f in findings:
        text = f.get("description", "") + " " + f.get("type", "")
        severity = f.get("severity", "medium")
        confidence = f.get("confidence", 0.5)
        weight = SEVERITY_WEIGHTS.get(severity.lower(), 0.25) * confidence

        found: Dict[str, Any] = {}

        for domain in DOMAIN_RE.findall(text):
            domain_lower = domain.lower()
            found[domain_lower] = {"value": domain_lower, "type": "domain", "_weight": weight}
            domain_counts[domain_lower] = domain_counts.get(domain_lower, 0) + 1

        for ip in IPV4_RE.findall(text):
            found[ip] = {"value": ip, "type": "ipv4", "_weight": weight}

        for h in HASH_RE.findall(text):
            found[h] = {"value": h, "type": "hash", "_weight": weight}

        for url in URL_RE.findall(text):
            found[url] = {"value": url, "type": "url", "_weight": weight}

        for ent in found.values():
            key = (ent["value"], ent["type"])
            ioc_counts[key] = ioc_counts.get(key, 0) + 1
            entities.append(ent)

    # Deduplicate entities list by (value, type)
    seen: Set[Tuple[str, str]] = set()
    deduped: List[Dict[str, Any]] = []
    for e in entities:
        k = (e["value"], e["type"])
        if k not in seen:
            seen.add(k)
            deduped.append(e)

    return deduped, domain_counts, ioc_counts


def _has_infra_hints(finding: Dict[str, Any]) -> bool:
    """Check if finding has infrastructure-related hints."""
    text = (finding.get("description", "") + " " + finding.get("type", "")).lower()
    hints = (
        "domain", "dns", "ip", "c2", "command", "control", "server",
        "host", "infrastructure", "tunnel", "callback", "beacon"
    )
    return any(h in text for h in hints)


def _find_campaign_hints(
    findings: List[Dict[str, Any]],
    _themes: Dict[str, List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """Find findings that may belong to the same campaign.

    Heuristic: same type appearing from multiple sources or
    high confidence + high severity cluster.
    """
    hints: List[Dict[str, Any]] = []

    # Cluster: same type, multiple sources → campaign signal
    type_sources: Dict[str, Set[str]] = {}
    for f in findings:
        type_sources.setdefault(f["type"], set()).add(f["source"])

    for ftype, srcs in type_sources.items():
        if len(srcs) >= 2:
            matching = [f for f in findings if f["type"] == ftype]
            avg_conf = sum(x["confidence"] for x in matching) / max(len(matching), 1)
            hints.append({
                "type": "multi_source_cluster",
                "finding_type": ftype,
                "sources": list(srcs),
                "count": len(matching),
                "avg_confidence": round(avg_conf, 2),
            })

    # High-confidence cluster (conf > 0.8, severity high/critical)
    high_conf_findings = [
        f for f in findings
        if f["confidence"] > 0.8 and f["severity"].lower() in ("high", "critical")
    ]
    if len(high_conf_findings) >= 2:
        hints.append({
            "type": "high_confidence_cluster",
            "count": len(high_conf_findings),
            "severities": [f["severity"] for f in high_conf_findings],
        })

    return hints


def _find_coupling_pairs(
    entities: List[Dict[str, Any]]
) -> List[Tuple[str, str]]:
    """Find entity pairs that appear in the same finding.

    Returns list of (entity1_value, entity2_value) tuples.
    """
    pairs: List[Tuple[str, str]] = []
    # Group entities by their source finding index (approximate via dedup key)
    # We pair entities of different types within the same pass
    by_type: Dict[str, List[str]] = {}
    for e in entities:
        by_type.setdefault(e["type"], []).append(e["value"])

    # Domain + IP pairs from same finding (heuristic: appear together in text)
    # Simplified: just cross-type pairs seen across entities list
    for dtype, dvals in list(by_type.items())[:2]:  # noqa: B007
        for itype, ivals in list(by_type.items())[1:]:  # noqa: B007
            for dv in dvals[:5]:
                for iv in ivals[:5]:
                    pairs.append((dv, iv))

    return list(set(pairs))[:20]


def _build_so_what(
    verdict: str,
    risk_score: float,
    top_themes: List[Tuple[str, float]],
    dominant_cluster: Optional[str],
    high_risk_count: int,
    anomaly_count: int,
    repeated_domains: List[str],
) -> str:
    """Build one-liner operator takeaway."""
    if verdict == "HIGH_RISK":
        parts = ["HIGH RISK detected"]
        if dominant_cluster:
            parts.append(f"cluster={dominant_cluster}")
        if high_risk_count > 0:
            parts.append(f"{high_risk_count} critical/high findings")
        if anomaly_count > 0:
            parts.append(f"{anomaly_count} anomalies")
        if repeated_domains:
            parts.append(f"repeated domains: {', '.join(repeated_domains[:3])}")
        return "; ".join(parts)
    elif verdict == "SUSPICIOUS":
        if top_themes:
            top = top_themes[0][0]
            return f"SUSPICIOUS: top theme={top}"
        return "SUSPICIOUS: review recommended"
    else:
        return "CLEAN: no significant threats detected"


def _derive_theme_key(finding: Dict[str, Any]) -> str:
    """Derive theme key from finding for grouping."""
    ftype = finding.get("type", "unknown").lower()
    source = str(finding.get("source", "unknown")).lower()

    # Known patterns → canonical themes
    if any(k in ftype for k in ("malware", "ransomware", "trojan", "virus")):
        return "malware_activity"
    if any(k in ftype for k in ("phishing", "social_engineering", "spoof")):
        return "phishing_campaign"
    if any(k in ftype for k in ("domain", "dns", "c2", "command_control")):
        return "infrastructure"
    if any(k in ftype for k in ("url", "uri", "link")):
        return "url_analysis"
    if any(k in ftype for k in ("file", "hash", "md5", "sha", "sample")):
        return "file_intel"
    if any(k in ftype for k in ("ip", "addr", "asn", "bgp")):
        return "network_intel"
    if any(k in ftype for k in ("leak", "breach", "exposed", "credentials")):
        return "data_breach"
    if any(k in ftype for k in ("vuln", "cve", "exploit", "patch")):
        return "vulnerability"
    if any(k in ftype for k in ("pattern", "correlation", "anomaly")):
        return f"pattern_{source}"
    return ftype


def _count_anomalies(findings: List[Dict[str, Any]]) -> int:
    """Count simple anomalies in findings."""
    count = 0
    for f in findings:
        desc = f.get("description", "").lower()
        if any(k in desc for k in ("future_timestamp", "clock_skew", "temporal", "anomaly")):
            count += 1
        if f.get("confidence", 0) < 0.3:
            count += 1
    return count


# =============================================================================
# SECOND-ORDER CONDENSATION HELPERS
# Bounded, fail-soft, cheap heuristics only
# =============================================================================

def _calc_cross_source_confidence(
    findings: List[Dict[str, Any]],
    theme_source_overlap: Dict[str, List[str]],
    campaign_hints: List[Dict[str, Any]],
) -> float:
    """Calculate 0.0-1.0 multi-source corroboration confidence.

    Signal: same IOC/indicator seen across multiple independent sources.
    """
    if not findings:
        return 0.0

    # Source diversity bonus
    sources = {f["source"] for f in findings}
    source_diversity = min(len(sources) / max(len(findings), 1), 1.0)

    # Theme overlap bonus (themes with 2+ sources)
    multi_source_themes = sum(1 for srcs in theme_source_overlap.values() if len(srcs) >= 2)
    theme_coverage = min(multi_source_themes / max(len(theme_source_overlap), 1), 1.0)

    # Campaign hints bonus
    campaign_bonus = 0.2 if campaign_hints else 0.0

    # Repeated IOC bonus (has corroboration)
    has_repeated = any(
        f.get("description", "") for f in findings
        if any(ioc in f.get("description", "").lower()
               for ioc in ("domain", "ip", "hash", "url"))
    )
    ioc_bonus = 0.15 if has_repeated else 0.0

    confidence = (
        source_diversity * 0.35 +
        theme_coverage * 0.30 +
        campaign_bonus +
        ioc_bonus
    )
    return round(min(confidence, 1.0), 2)


def _get_corroborated_iocs(
    findings: List[Dict[str, Any]],
    repeated_iocs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return IOCs that appear with 2+ source evidence.

    Corroborated = repeated across findings + high severity + high confidence.
    """
    if not repeated_iocs:
        return []

    corroborated: List[Dict[str, Any]] = []
    for ioc in repeated_iocs[:10]:  # bounded to 10
        value = ioc.get("value", "")
        ioc_type = ioc.get("type", "unknown")
        count = ioc.get("count", 1)

        # Find matching findings
        matching = [
            f for f in findings
            if value.lower() in f.get("description", "").lower()
        ]

        if len(matching) >= 2:
            avg_conf = sum(f.get("confidence", 0.5) for f in matching) / max(len(matching), 1)
            max_sev = max(
                SEVERITY_WEIGHTS.get(f.get("severity", "medium").lower(), 0.25)
                for f in matching
            )
            corroborated.append({
                "value": value,
                "type": ioc_type,
                "source_count": len(matching),
                "confidence": round(avg_conf, 2),
                "severity_weight": round(max_sev, 2),
                "actionable": avg_conf >= 0.7 and max_sev >= 0.5,
            })

    # Sort by source_count desc, then confidence desc
    corroborated.sort(key=lambda x: (-x["source_count"], -x["confidence"]))
    return corroborated[:8]  # hard cap


def _get_top_priority_pivots(
    findings: List[Dict[str, Any]],
    dominant_cluster: Optional[str],
    high_risk_branch: List[Dict[str, Any]],
    repeated_domains: List[str],
) -> List[Dict[str, Any]]:
    """Build bounded priority shortlist for operator.

    Max 5 pivots. Prioritizes: infra-heavy, corroborated, high-severity.
    """
    pivots: List[Dict[str, Any]] = []

    # 1. Dominant cluster gets first slot
    if dominant_cluster:
        pivots.append({
            "pivot_type": "dominant_cluster",
            "description": f"Primary cluster: {dominant_cluster}",
            "priority": 1,
        })

    # 2. High-risk findings with infra
    for f in high_risk_branch[:2]:
        if len(pivots) >= 5:
            break
        pivots.append({
            "pivot_type": "high_risk_infra",
            "value": _extract_primary_entity(f),
            "description": f.get("description", "")[:120],
            "severity": f.get("severity", "medium"),
            "priority": 2,
        })

    # 3. Repeated domains (infra signal)
    for domain in repeated_domains[:2]:
        if len(pivots) >= 5:
            break
        pivots.append({
            "pivot_type": "repeated_domain",
            "value": domain,
            "description": f"Domain seen across multiple findings",
            "priority": 3,
        })

    # 4. High-confidence IOCs
    for f in findings:
        if len(pivots) >= 5:
            break
        if f.get("confidence", 0) >= 0.85 and f.get("severity", "").lower() in ("high", "critical"):
            entity = _extract_primary_entity(f)
            if entity and not any(p.get("value") == entity for p in pivots):
                pivots.append({
                    "pivot_type": "high_conf_ioc",
                    "value": entity,
                    "description": f.get("description", "")[:120],
                    "confidence": f.get("confidence"),
                    "priority": 4,
                })

    return pivots[:5]


def _calc_campaign_confidence(
    campaign_hints: List[Dict[str, Any]],
    theme_source_overlap: Dict[str, List[str]],
) -> float:
    """Calculate 0.0-1.0 campaign cluster confidence.

    Evidence: multi_source_cluster hints + overlapping themes across sources.
    """
    if not campaign_hints:
        return 0.0

    # Multi-source cluster evidence
    multi_source_signals = [
        h for h in campaign_hints
        if h.get("type") == "multi_source_cluster"
    ]

    # High-confidence cluster evidence
    high_conf_signals = [
        h for h in campaign_hints
        if h.get("type") == "high_confidence_cluster"
    ]

    # Theme overlap factor
    overlapping_themes = sum(
        1 for srcs in theme_source_overlap.values()
        if len(srcs) >= 2
    )
    overlap_factor = min(overlapping_themes / 3.0, 0.4)  # cap at 0.4

    # Calculate
    multi_source_score = min(len(multi_source_signals) * 0.25, 0.4)
    high_conf_score = min(len(high_conf_signals) * 0.15, 0.2)

    confidence = multi_source_score + high_conf_score + overlap_factor
    return round(min(confidence, 1.0), 2)


def _extract_primary_entity(finding: Dict[str, Any]) -> str:
    """Extract primary IOC entity from finding description."""
    import re

    text = finding.get("description", "") + " " + finding.get("type", "")

    # Try domain first (infra signal)
    domain_match = re.search(
        r'\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b',
        text, re.IGNORECASE
    )
    if domain_match:
        return domain_match.group(0)

    # Try IP
    ip_match = re.search(
        r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d{1,2})\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d{1,2})\b',
        text
    )
    if ip_match:
        return ip_match.group(0)

    # Try hash
    hash_match = re.search(
        r'\b[a-f0-9]{32,}\b', text, re.IGNORECASE
    )
    if hash_match:
        return hash_match.group(0)[:32]

    return ""


# =============================================================================
# OPERATOR SHORTLIST — SECOND-ORDER CONDENSATION HELPERS
# Bounded, fail-soft, pure functions
# =============================================================================

def _get_what_matters_first(
    verdict: str,
    dominant_cluster: Optional[str],
    high_risk_branch: List[Dict[str, Any]],
    corroborated_iocs: List[Dict[str, Any]],
) -> str:
    """Return single primary action/takeaway for operator."""
    if verdict == "HIGH_RISK":
        if dominant_cluster:
            return f"Investigate cluster '{dominant_cluster}' — highest-risk theme"
        if high_risk_branch:
            return f"Pivot on {len(high_risk_branch)} critical/high findings with infra signals"
        if corroborated_iocs:
            top = corroborated_iocs[0]
            return f"Corroborated IOC: {top.get('type','ioc')}={top.get('value','?')} (sources={top.get('source_count',1)})"
        return "HIGH_RISK verdict — review all high-severity findings"
    elif verdict == "SUSPICIOUS":
        if dominant_cluster:
            return f"Monitor cluster '{dominant_cluster}' for escalation"
        return "Anomalies detected — verify with additional sources"
    return "No immediate action required"


def _build_operator_shortlist(
    dominant_cluster: Optional[str],
    high_risk_branch: List[Dict[str, Any]],
    corroborated_iocs: List[Dict[str, Any]],
    risk_score: float,
) -> List[Dict[str, Any]]:
    """Build max-3 bounded prioritised shortlist for scheduler/export.

    Returns items with: pivot_type, value, reason, priority
    """
    shortlist: List[Dict[str, Any]] = []

    # 1. Dominant cluster (always first if present)
    if dominant_cluster:
        shortlist.append({
            "pivot_type": "dominant_cluster",
            "value": dominant_cluster,
            "reason": "highest-risk theme with most critical findings",
            "priority": 1,
        })

    # 2. Top corroborated IOC (infra signal with multi-source evidence)
    for ioc in corroborated_iocs[:2]:
        if len(shortlist) >= 3:
            break
        shortlist.append({
            "pivot_type": "corroborated_ioc",
            "value": ioc.get("value", ""),
            "reason": f"{ioc.get('type')} seen across {ioc.get('source_count',1)} sources (conf={ioc.get('confidence',0):.2f})",
            "priority": 2,
        })

    # 3. High-risk with infra
    for f in high_risk_branch[:2]:
        if len(shortlist) >= 3:
            break
        shortlist.append({
            "pivot_type": "high_risk_infra",
            "value": _extract_primary_entity(f),
            "reason": f["severity"].upper() + " + infra hint",
            "priority": 3,
        })

    return shortlist[:3]


def _build_confidence_note(
    risk_score: float,
    cross_source_confidence: float,
    campaign_confidence: float,
) -> str:
    """Human-readable confidence explanation."""
    if risk_score >= 0.7:
        base = "HIGH RISK verdict"
    elif risk_score >= 0.3:
        base = "SUSPICIOUS verdict"
    else:
        base = "CLEAN verdict"

    if cross_source_confidence >= 0.6:
        corroboration = "strong multi-source corroboration"
    elif cross_source_confidence >= 0.3:
        corroboration = "moderate cross-source agreement"
    else:
        corroboration = "limited source corroboration"

    if campaign_confidence >= 0.5:
        campaign = "campaign cluster likely"
    elif campaign_confidence >= 0.2:
        campaign = "possible campaign cluster"
    else:
        campaign = "no campaign signals"

    return f"{base} | {corroboration} | {campaign}"


def _classify_signal_quality(
    risk_score: float,
    cross_source_confidence: float,
    campaign_confidence: float,
    corroborated_iocs: List[Dict[str, Any]],
) -> str:
    """Classify signal as strong/mixed/weak for scheduler filtering."""
    strong_indicators = (
        risk_score >= 0.6 and
        cross_source_confidence >= 0.5 and
        len(corroborated_iocs) >= 2
    )
    weak_indicators = (
        risk_score < 0.3 and
        cross_source_confidence < 0.2 and
        not corroborated_iocs
    )

    if strong_indicators:
        return "strong"
    elif weak_indicators:
        return "weak"
    return "mixed"


def create_workflow_orchestrator(
    orchestrator: Any,
    config: Optional[IntelligenceConfig] = None
) -> WorkflowOrchestrator:
    """Create a configured WorkflowOrchestrator instance.

    Args:
        orchestrator: Main orchestrator instance
        config: Optional intelligence configuration

    Returns:
        Configured WorkflowOrchestrator instance
    """
    return WorkflowOrchestrator(orchestrator, config)
