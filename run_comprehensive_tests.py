#!/usr/bin/env python3
"""Comprehensive test runner for Hledac autonomous research system.

This module provides a comprehensive test runner with detailed reporting,
including HTML reports, JSON summaries, performance benchmarks, and memory usage.
"""

import json
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil

# Test suites configuration: (name, file, timeout_seconds)
SUITES: list[tuple[str, str, int]] = [
    ("Unit Tests (Basic)", "test_autonomous_analyzer.py", 60),
    ("Unit Tests (Extended)", "test_autonomous_analyzer_extended.py", 120),
    ("ToT Integration", "test_tot_integration.py", 120),
    ("Integration Tests", "test_integration_autonomous.py", 300),
    ("Performance Tests", "test_performance.py", 300),
    ("Real-World Scenarios", "test_scenarios.py", 180),
    ("Stress Tests", "test_stress.py", 600),
]

# ANSI color codes
COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "green": "\033[92m",
    "red": "\033[91m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "cyan": "\033[96m",
    "magenta": "\033[95m",
    "white": "\033[97m",
    "dim": "\033[2m",
}


def color(name: str, text: str) -> str:
    """Apply color to text if terminal supports it."""
    if sys.stdout.isatty():
        return f"{COLORS.get(name, '')}{text}{COLORS['reset']}"
    return text


def bold(text: str) -> str:
    """Make text bold."""
    return color("bold", text)


def green(text: str) -> str:
    """Make text green."""
    return color("green", text)


def red(text: str) -> str:
    """Make text red."""
    return color("red", text)


def yellow(text: str) -> str:
    """Make text yellow."""
    return color("yellow", text)


def blue(text: str) -> str:
    """Make text blue."""
    return color("blue", text)


def cyan(text: str) -> str:
    """Make text cyan."""
    return color("cyan", text)


def dim(text: str) -> str:
    """Make text dim."""
    return color("dim", text)


@dataclass
class SuiteResult:
    """Result of a single test suite execution."""
    name: str
    file: str
    status: str = "pending"  # pending, running, passed, failed, skipped, timeout
    tests_total: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0
    tests_error: int = 0
    duration: float = 0.0
    coverage: float | None = None
    output: str = ""
    error_output: str = ""
    html_report: str | None = None
    json_report: str | None = None
    exit_code: int = 0
    memory_peak_mb: float = 0.0
    start_time: datetime | None = None
    end_time: datetime | None = None


@dataclass
class MemorySnapshot:
    """Memory usage snapshot."""
    phase: str
    timestamp: datetime
    memory_mb: float
    rss_mb: float
    vms_mb: float


@dataclass
class PerformanceBenchmark:
    """Performance benchmark data."""
    query_type: str
    estimated_seconds: float
    actual_seconds: float | None = None
    variance_percent: float | None = None


class MemoryMonitor:
    """Monitor memory usage during test execution."""

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.snapshots: list[MemorySnapshot] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process = psutil.Process()
        self._peak_memory = 0.0

    def start(self):
        """Start memory monitoring in background thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

    def stop(self) -> float:
        """Stop monitoring and return peak memory in MB."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        return self._peak_memory

    def _monitor(self):
        """Monitor memory usage."""
        while not self._stop_event.is_set():
            try:
                memory_info = self._process.memory_info()
                memory_mb = memory_info.rss / (1024 * 1024)
                self._peak_memory = max(self._peak_memory, memory_mb)
                self.snapshots.append(MemorySnapshot(
                    phase="monitoring",
                    timestamp=datetime.now(),
                    memory_mb=memory_mb,
                    rss_mb=memory_info.rss / (1024 * 1024),
                    vms_mb=memory_info.vms / (1024 * 1024),
                ))
            except psutil.Error:
                pass
            time.sleep(self.interval)

    def record_phase(self, phase: str):
        """Record a named phase snapshot."""
        try:
            memory_info = self._process.memory_info()
            memory_mb = memory_info.rss / (1024 * 1024)
            self._peak_memory = max(self._peak_memory, memory_mb)
            self.snapshots.append(MemorySnapshot(
                phase=phase,
                timestamp=datetime.now(),
                memory_mb=memory_mb,
                rss_mb=memory_info.rss / (1024 * 1024),
                vms_mb=memory_info.vms / (1024 * 1024),
            ))
        except psutil.Error:
            pass


class TestSuiteRunner:
    """Comprehensive test suite runner with detailed reporting."""

    def __init__(self, test_dir: Path | None = None, verbose: bool = True):
        self.test_dir = test_dir or Path(__file__).parent
        self.report_dir = self.test_dir / "test_reports"
        self.report_dir.mkdir(exist_ok=True)
        self.results: list[SuiteResult] = []
        self.start_time: datetime | None = None
        self.end_time: datetime | None = None
        self.memory_monitor = MemoryMonitor()
        self.memory_snapshots: list[MemorySnapshot] = []
        self.performance_benchmarks: list[PerformanceBenchmark] = []
        self.verbose = verbose
        self._interrupted = False

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle interrupt signals gracefully."""
        self._interrupted = True
        print(f"\n{yellow('⚠️  Interrupted by user')}")
        self._print_summary()
        sys.exit(130)

    def _print_header(self):
        """Print test run header."""
        print()
        print(bold("═" * 80))
        print(bold("🧪 HLEDAC COMPREHENSIVE TEST SUITE v1.0").center(80))
        print(f"Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}".center(80))
        print(bold("═" * 80))
        print()

    def _print_suite_header(self, name: str, file: str, timeout: int):
        """Print suite execution header."""
        print("┌" + "─" * 78 + "┐")
        print(f"│ {blue('Running:')} {bold(name):<69}│")
        print(f"│ {dim('File:')} {file:<72}│")
        print(f"│ {dim('Timeout:')} {timeout}s{' ':<66}│")
        print("└" + "─" * 78 + "┘")
        print()

    def _print_suite_result(self, result: SuiteResult):
        """Print suite execution result."""
        if result.status == "passed":
            status_icon = green("✅ PASSED")
        elif result.status == "failed":
            status_icon = red("❌ FAILED")
        elif result.status == "timeout":
            status_icon = yellow("⏱️  TIMEOUT")
        elif result.status == "skipped":
            status_icon = yellow("⏭️  SKIPPED")
        else:
            status_icon = red("❌ ERROR")

        coverage_str = f"{result.coverage:.0f}%" if result.coverage else "N/A"

        print()
        print(f"{status_icon} ({result.tests_passed}/{result.tests_total} tests, {result.duration:.1f}s)")
        if result.coverage:
            print(f"   Coverage: {coverage_str}")
        if result.memory_peak_mb > 0:
            print(f"   Peak Memory: {result.memory_peak_mb:.0f} MB")
        print()

        if result.tests_failed > 0 and result.error_output:
            print(dim("Failed tests output:"))
            print(dim(result.error_output[:2000]))  # Limit output
            print()

    def _parse_pytest_output(self, output: str, result: SuiteResult):
        """Parse pytest output to extract test counts."""
        # Look for summary line like "24 passed, 2 failed, 1 skipped in 5.32s"
        import re

        # Parse test counts
        patterns = [
            (r'(\d+)\s+passed', 'tests_passed'),
            (r'(\d+)\s+failed', 'tests_failed'),
            (r'(\d+)\s+skipped', 'tests_skipped'),
            (r'(\d+)\s+error', 'tests_error'),
        ]

        for pattern, attr in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                setattr(result, attr, int(match.group(1)))

        # Calculate total
        result.tests_total = (result.tests_passed + result.tests_failed +
                            result.tests_skipped + result.tests_error)

        # Parse duration from summary line
        duration_match = re.search(r'in\s+([\d.]+)s', output)
        if duration_match:
            result.duration = float(duration_match.group(1))

        # Parse coverage if present
        coverage_match = re.search(r'coverage.*?([\d.]+)%', output, re.IGNORECASE)
        if coverage_match:
            result.coverage = float(coverage_match.group(1))

    def _generate_html_report(self, result: SuiteResult) -> Path:
        """Generate HTML report for a test suite."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_file = self.report_dir / f"{result.file.replace('.py', '')}_{timestamp}.html"

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Test Report: {result.name}</title>
    <style>
        :root {{
            --bg-primary: #1a1a2e;
            --bg-secondary: #16213e;
            --bg-tertiary: #0f3460;
            --text-primary: #eaeaea;
            --text-secondary: #a0a0a0;
            --success: #4ecca3;
            --warning: #f4d03f;
            --danger: #e74c3c;
            --info: #3498db;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            padding: 2rem;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        header {{
            background: var(--bg-secondary);
            padding: 2rem;
            border-radius: 12px;
            margin-bottom: 2rem;
            border-left: 4px solid var(--info);
        }}
        h1 {{ font-size: 1.8rem; margin-bottom: 0.5rem; }}
        .subtitle {{ color: var(--text-secondary); }}
        .status-badge {{
            display: inline-block;
            padding: 0.5rem 1rem;
            border-radius: 20px;
            font-weight: bold;
            font-size: 0.9rem;
            margin-top: 1rem;
        }}
        .status-passed {{ background: var(--success); color: #1a1a2e; }}
        .status-failed {{ background: var(--danger); color: white; }}
        .status-timeout {{ background: var(--warning); color: #1a1a2e; }}
        .status-skipped {{ background: var(--text-secondary); color: white; }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }}
        .metric-card {{
            background: var(--bg-secondary);
            padding: 1.5rem;
            border-radius: 8px;
            text-align: center;
        }}
        .metric-value {{
            font-size: 2rem;
            font-weight: bold;
            color: var(--info);
        }}
        .metric-label {{ color: var(--text-secondary); font-size: 0.9rem; }}
        .test-breakdown {{
            background: var(--bg-secondary);
            padding: 1.5rem;
            border-radius: 8px;
            margin-bottom: 2rem;
        }}
        .test-bar {{
            display: flex;
            height: 30px;
            border-radius: 15px;
            overflow: hidden;
            margin-top: 1rem;
        }}
        .bar-passed {{ background: var(--success); }}
        .bar-failed {{ background: var(--danger); }}
        .bar-skipped {{ background: var(--warning); }}
        .bar-error {{ background: var(--danger); opacity: 0.7; }}
        .output-section {{
            background: var(--bg-secondary);
            padding: 1.5rem;
            border-radius: 8px;
            margin-bottom: 2rem;
        }}
        pre {{
            background: var(--bg-tertiary);
            padding: 1rem;
            border-radius: 4px;
            overflow-x: auto;
            font-size: 0.85rem;
            line-height: 1.5;
            max-height: 500px;
            overflow-y: auto;
        }}
        .timestamp {{
            color: var(--text-secondary);
            font-size: 0.85rem;
            margin-top: 2rem;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🧪 {result.name}</h1>
            <p class="subtitle">Test File: {result.file}</p>
            <span class="status-badge status-{result.status}">{result.status.upper()}</span>
        </header>

        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-value">{result.tests_total}</div>
                <div class="metric-label">Total Tests</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" style="color: var(--success)">{result.tests_passed}</div>
                <div class="metric-label">Passed</div>
            </div>
            <div class="metric-card">
                <div class="metric-value" style="color: var(--danger)">{result.tests_failed}</div>
                <div class="metric-label">Failed</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">{result.duration:.2f}s</div>
                <div class="metric-label">Duration</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">{result.coverage:.0f}%</div>
                <div class="metric-label">Coverage</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">{result.memory_peak_mb:.0f}</div>
                <div class="metric-label">Peak Memory (MB)</div>
            </div>
        </div>

        <div class="test-breakdown">
            <h3>Test Breakdown</h3>
            <div class="test-bar">
                <div class="bar-passed" style="width: {(result.tests_passed / max(result.tests_total, 1)) * 100}%"></div>
                <div class="bar-failed" style="width: {(result.tests_failed / max(result.tests_total, 1)) * 100}%"></div>
                <div class="bar-skipped" style="width: {(result.tests_skipped / max(result.tests_total, 1)) * 100}%"></div>
                <div class="bar-error" style="width: {(result.tests_error / max(result.tests_total, 1)) * 100}%"></div>
            </div>
            <p style="margin-top: 1rem; color: var(--text-secondary);">
                Passed: {result.tests_passed} | Failed: {result.tests_failed} |
                Skipped: {result.tests_skipped} | Errors: {result.tests_error}
            </p>
        </div>

        <div class="output-section">
            <h3>Test Output</h3>
            <pre>{self._escape_html(result.output)}</pre>
        </div>

        {f'<div class="output-section"><h3>Error Output</h3><pre>{self._escape_html(result.error_output)}</pre></div>' if result.error_output else ''}

        <p class="timestamp">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
</body>
</html>"""

        html_file.write_text(html_content, encoding="utf-8")
        return html_file

    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters."""
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#x27;"))

    def _generate_json_summary(self) -> Path:
        """Generate JSON summary of all test results."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_file = self.report_dir / f"test_summary_{timestamp}.json"

        summary = {
            "metadata": {
                "version": "1.0",
                "started": self.start_time.isoformat() if self.start_time else None,
                "completed": self.end_time.isoformat() if self.end_time else None,
                "total_duration": (self.end_time - self.start_time).total_seconds() if self.end_time and self.start_time else 0,
            },
            "summary": {
                "total_suites": len(SUITES),
                "passed_suites": sum(1 for r in self.results if r.status == "passed"),
                "failed_suites": sum(1 for r in self.results if r.status == "failed"),
                "skipped_suites": sum(1 for r in self.results if r.status == "skipped"),
                "timeout_suites": sum(1 for r in self.results if r.status == "timeout"),
                "total_tests": sum(r.tests_total for r in self.results),
                "total_passed": sum(r.tests_passed for r in self.results),
                "total_failed": sum(r.tests_failed for r in self.results),
                "total_skipped": sum(r.tests_skipped for r in self.results),
                "total_errors": sum(r.tests_error for r in self.results),
            },
            "suites": [
                {
                    "name": r.name,
                    "file": r.file,
                    "status": r.status,
                    "tests_total": r.tests_total,
                    "tests_passed": r.tests_passed,
                    "tests_failed": r.tests_failed,
                    "tests_skipped": r.tests_skipped,
                    "tests_error": r.tests_error,
                    "duration": r.duration,
                    "coverage": r.coverage,
                    "output": r.output,
                    "error_output": r.error_output,
                    "html_report": r.html_report,
                    "json_report": r.json_report,
                    "exit_code": r.exit_code,
                    "memory_peak_mb": r.memory_peak_mb,
                    "start_time": r.start_time.isoformat() if r.start_time else None,
                    "end_time": r.end_time.isoformat() if r.end_time else None,
                }
                for r in self.results
            ],
            "memory": {
                "snapshots": [
                    {
                        "phase": s.phase,
                        "timestamp": s.timestamp.isoformat(),
                        "memory_mb": s.memory_mb,
                        "rss_mb": s.rss_mb,
                        "vms_mb": s.vms_mb,
                    }
                    for s in self.memory_snapshots
                ],
                "peak_mb": max((s.memory_mb for s in self.memory_snapshots), default=0),
            },
            "performance_benchmarks": [
                {
                    "query_type": b.query_type,
                    "estimated_seconds": b.estimated_seconds,
                    "actual_seconds": b.actual_seconds,
                    "variance_percent": b.variance_percent,
                }
                for b in self.performance_benchmarks
            ],
        }

        json_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return json_file

    def _print_summary(self):
        """Print final summary table."""
        print()
        print(bold("═" * 80))
        print(bold("📊 FINAL SUMMARY").center(80))
        print(bold("═" * 80))
        print()

        # Header
        print(f"{'Suite':<35} │ {'Status':<8} │ {'Tests':<7} │ {'Time':<8} │ {'Coverage':<10}")
        print("─" * 35 + "┼" + "─" * 10 + "┼" + "─" * 9 + "┼" + "─" + "─" * 10 + "┼" + "─" * 12)

        # Rows
        for result in self.results:
            if result.status == "passed":
                status = green("✅ PASS")
            elif result.status == "failed":
                status = red("❌ FAIL")
            elif result.status == "timeout":
                status = yellow("⏱️  T/O")
            elif result.status == "skipped":
                status = yellow("⏭️  SKIP")
            else:
                status = dim("PENDING")

            tests = f"{result.tests_passed}/{result.tests_total}"
            time_str = f"{result.duration:.1f}s"
            coverage = f"{result.coverage:.0f}%" if result.coverage else "N/A"

            print(f"{result.name:<35} │ {status:<14} │ {tests:<7} │ {time_str:<8} │ {coverage:<10}")

        print()

        # Totals
        total_suites = len(self.results)
        passed_suites = sum(1 for r in self.results if r.status == "passed")
        total_tests = sum(r.tests_total for r in self.results)
        total_passed = sum(r.tests_passed for r in self.results)
        total_duration = sum(r.duration for r in self.results)

        success_rate = (passed_suites / total_suites * 100) if total_suites > 0 else 0

        if success_rate == 100:
            status_text = green(f"{passed_suites}/{total_suites} suites passed (100%)")
        elif success_rate >= 80:
            status_text = yellow(f"{passed_suites}/{total_suites} suites passed ({success_rate:.0f}%)")
        else:
            status_text = red(f"{passed_suites}/{total_suites} suites passed ({success_rate:.0f}%)")

        print(f"Total: {status_text}")
        print(f"Total Tests: {total_passed}/{total_tests} passed")
        print(f"Total Time: {total_duration:.1f}s")
        print()

    def _print_performance_table(self):
        """Print performance benchmarks."""
        if not self.performance_benchmarks:
            return

        print(bold("═" * 80))
        print(bold("📈 PERFORMANCE BENCHMARKS").center(80))
        print(bold("═" * 80))
        print()

        print(f"{'Query Type':<25} │ {'Estimated':<12} │ {'Actual':<10} │ {'Variance':<12}")
        print("─" * 25 + "┼" + "─" * 14 + "┼" + "─" * 12 + "┼" + "─" * 14)

        for bench in self.performance_benchmarks:
            estimated = f"{bench.estimated_seconds:.0f}s"
            actual = f"{bench.actual_seconds:.0f}s" if bench.actual_seconds else "N/A"

            if bench.variance_percent is not None:
                variance = f"{bench.variance_percent:+.0f}%"
                if bench.variance_percent < 0:
                    variance = green(variance + " (faster)")
                elif bench.variance_percent > 20:
                    variance = red(variance + " (slower)")
                else:
                    variance = yellow(variance)
            else:
                variance = "N/A"

            print(f"{bench.query_type:<25} │ {estimated:<12} │ {actual:<10} │ {variance:<12}")

        print()

    def _print_memory_report(self):
        """Print memory usage report."""
        if not self.memory_snapshots:
            return

        print(bold("═" * 80))
        print(bold("💾 MEMORY REPORT").center(80))
        print(bold("═" * 80))
        print()

        print(f"{'Phase':<25} │ {'Memory (MB)':<15}")
        print("─" * 25 + "┼" + "─" * 17)

        # Group by phase and show latest for each
        phase_memory = {}
        for snapshot in self.memory_snapshots:
            phase_memory[snapshot.phase] = snapshot.memory_mb

        for phase, memory in phase_memory.items():
            print(f"{phase:<25} │ {memory:>10.0f}")

        # Peak memory
        peak_memory = max(s.memory_mb for s in self.memory_snapshots)
        memory_limit = 5500  # MB

        print()
        if peak_memory <= memory_limit:
            print(f"Peak Memory: {green(f'{peak_memory:.0f} MB')} (under {memory_limit} MB limit ✅)")
        else:
            print(f"Peak Memory: {red(f'{peak_memory:.0f} MB')} (exceeded {memory_limit} MB limit ❌)")
        print()

    def _discover_test_file(self, filename: str) -> Path | None:
        """Discover test file in various locations."""
        # Try different search paths
        search_paths = [
            self.test_dir / filename,
            self.test_dir / "tests" / filename,
            Path("tests") / filename,
            Path("test") / filename,
            Path(filename),
        ]

        for path in search_paths:
            if path.exists():
                return path.resolve()

        return None

    def run_suite(self, name: str, file: str, timeout: int) -> SuiteResult:
        """Run a single test suite."""
        result = SuiteResult(name=name, file=file)
        result.start_time = datetime.now()

        # Discover test file
        test_file = self._discover_test_file(file)
        if not test_file:
            result.status = "skipped"
            result.error_output = f"Test file not found: {file}"
            result.end_time = datetime.now()
            return result

        # Generate report paths
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        html_path = self.report_dir / f"{file.replace('.py', '')}_{timestamp}.html"
        json_path = self.report_dir / f"{file.replace('.py', '')}_{timestamp}.json"

        # Build pytest command
        cmd = [
            sys.executable, "-m", "pytest",
            str(test_file),
            "-v",
            "--tb=short",
            f"--html={html_path}",
            "--json-report",
            f"--json-report-file={json_path}",
            "--color=yes",
        ]

        # Add coverage if pytest-cov is available
        try:
            subprocess.run([sys.executable, "-m", "pytest", "--version"],
                         capture_output=True, check=True)
            cmd.extend(["--cov=hledac", "--cov-report=term-missing"])
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Start memory monitoring
        self.memory_monitor.start()

        try:
            # Run pytest
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            result.exit_code = process.returncode
            result.output = process.stdout
            result.error_output = process.stderr

            # Parse output
            self._parse_pytest_output(process.stdout, result)

            # Determine status
            if result.exit_code == 0:
                result.status = "passed"
            elif result.exit_code == 5:  # No tests collected
                result.status = "skipped"
            else:
                result.status = "failed"

        except subprocess.TimeoutExpired:
            result.status = "timeout"
            result.error_output = f"Test suite timed out after {timeout} seconds"
        except Exception as e:
            result.status = "failed"
            result.error_output = str(e)

        # Stop memory monitoring
        result.memory_peak_mb = self.memory_monitor.stop()
        self.memory_snapshots.extend(self.memory_monitor.snapshots)

        # Record end time
        result.end_time = datetime.now()

        # Generate HTML report
        if result.status != "skipped":
            html_file = self._generate_html_report(result)
            result.html_report = str(html_file)

        return result

    def run_all(self) -> bool:
        """Run all test suites."""
        self.start_time = datetime.now()
        self._print_header()

        # Record initial memory
        self.memory_monitor.record_phase("Initial")

        for name, file, timeout in SUITES:
            if self._interrupted:
                break

            self._print_suite_header(name, file, timeout)

            result = self.run_suite(name, file, timeout)
            self.results.append(result)

            self._print_suite_result(result)

            # Record memory after suite
            self.memory_monitor.record_phase(f"After {name}")

        self.end_time = datetime.now()

        # Print all reports
        self._print_summary()
        self._print_performance_table()
        self._print_memory_report()

        # Generate JSON summary
        summary_file = self._generate_json_summary()
        print(f"📄 JSON Summary: {cyan(str(summary_file))}")
        print(f"📁 HTML Reports: {cyan(str(self.report_dir))}")
        print()

        # Return success if all suites passed
        return all(r.status == "passed" for r in self.results)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Comprehensive test runner for Hledac autonomous research system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_comprehensive_tests.py
  python run_comprehensive_tests.py --test-dir ./tests
  python run_comprehensive_tests.py --suite "Unit Tests (Basic)"
        """
    )

    parser.add_argument(
        "--test-dir",
        type=Path,
        default=None,
        help="Directory containing test files (default: script directory)",
    )
    parser.add_argument(
        "--suite",
        type=str,
        default=None,
        help="Run only a specific test suite by name",
    )
    parser.add_argument(
        "--list-suites",
        action="store_true",
        help="List available test suites and exit",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="Directory for test reports (default: test_reports/)",
    )

    args = parser.parse_args()

    if args.list_suites:
        print(bold("Available Test Suites:"))
        print()
        for name, file, timeout in SUITES:
            print(f"  {cyan(name)}")
            print(f"    File: {file}")
            print(f"    Timeout: {timeout}s")
            print()
        return 0

    # Create runner
    runner = TestSuiteRunner(test_dir=args.test_dir)

    if args.report_dir:
        runner.report_dir = args.report_dir
        runner.report_dir.mkdir(exist_ok=True)

    # Run specific suite or all
    if args.suite:
        # Find suite
        for name, file, timeout in SUITES:
            if name == args.suite:
                runner.start_time = datetime.now()
                runner._print_header()
                result = runner.run_suite(name, file, timeout)
                runner.results.append(result)
                runner._print_suite_result(result)
                runner.end_time = datetime.now()
                runner._print_summary()
                return 0 if result.status == "passed" else 1
        print(red(f"Suite not found: {args.suite}"))
        return 1
    else:
        success = runner.run_all()
        return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
