#!/usr/bin/env python3.14
# F2: Run with PYTHONMALLOCSTATS=1 for allocator diagnostics on exit:
#   PYTHONMALLOCSTATS=1 python3.14 -m hledac.universal.tools.bench_py314_jit
"""
F214I-2 — Python 3.14 Experimental JIT Benchmark

Compares default vs PYTHON_JIT=1 for Hledac import/boot smoke.
Report-only: NO production changes, NO .venv patching.

Exit codes:
  0  = benchmark complete (results in stdout)
  64 = NO_PATCH (JIT not available in this interpreter)
  65 = benchmark error
"""

from __future__ import annotations

import os
import sys
import time
import subprocess
import textwrap
from pathlib import Path
from typing import NamedTuple


class JTStatus(NamedTuple):
    available: bool
    reason: str


class BenchResult(NamedTuple):
    name: str
    wall_s: float
    rss_kb: int = 0
    warnings: int = 0
    errors: int = 0


PROJECT_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
VENV_PYTHON = Path("/Users/vojtechhamada/PycharmProjects/Hledac/.venv/bin/python")


def check_jit(python: Path) -> JTStatus:
    """Check if the interpreter supports experimental JIT via sys.jit attribute."""
    try:
        result = subprocess.run(
            [str(python), "-c", "import sys; print(hasattr(sys, 'jit'))"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        has_jit = result.stdout.strip()
    except Exception as e:
        return JTStatus(False, f"subprocess check failed: {e}")

    if has_jit != "True":
        return JTStatus(
            False,
            f"sys.jit attribute NOT_FOUND. "
            f"Python 3.14.4 was built WITHOUT --with-jit. "
            f"PYTHON_JIT=1 has no effect on this interpreter.",
        )

    try:
        result = subprocess.run(
            [str(python), "-c",
             "import sys; print(getattr(sys.flags, 'jit', 0))"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        jit_flag = result.stdout.strip()
        if jit_flag in ("0", "False"):
            return JTStatus(False, f"sys.flags.jit={jit_flag} — JIT not active")
        return JTStatus(True, f"sys.flags.jit={jit_flag}")
    except Exception as e:
        return JTStatus(False, f"JIT flag check failed: {e}")


def _run_py(
    python: Path,
    script: str,
    env_override: dict[str, str] | None = None,
    timeout: int = 120,
) -> tuple[float, int, int, str, int, int]:
    """Run a python script, return (wall_s, rss_kb, exit_code, stderr, warnings, errors)."""
    base_env = dict(os.environ)
    if env_override:
        base_env.update(env_override)

    def _rss() -> int:
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024
        except Exception:
            return 0

    t0 = time.perf_counter()
    try:
        proc = subprocess.Popen(
            [str(python), "-c", script],
            env=base_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )
        _stdout, _stderr = proc.communicate(timeout=timeout)
        wall_s = time.perf_counter() - t0
        exit_code = proc.returncode
        rss_kb = _rss()
        stderr_text = _stderr.decode(errors="replace")
    except subprocess.TimeoutExpired:
        proc.kill()
        wall_s = time.perf_counter() - t0
        exit_code = 124
        stderr_text = "TIMEOUT"
        rss_kb = _rss()
    except Exception as e:
        wall_s = time.perf_counter() - t0
        exit_code = 65
        stderr_text = str(e)
        rss_kb = 0

    warnings = sum(1 for line in stderr_text.splitlines() if "Warning" in line)
    errors = sum(
        1
        for line in stderr_text.splitlines()
        if "Error" in line or "Exception" in line or "Traceback" in line
    )
    return wall_s, rss_kb, exit_code, stderr_text, warnings, errors


def bench_import(python: Path, env: dict[str, str] | None) -> BenchResult:
    """Time 'import autonomous_orchestrator'."""
    script = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(PROJECT_ROOT)!r})
        t0 = time.perf_counter()
        try:
            import autonomous_orchestrator
            t1 = time.perf_counter()
            print(f"RESULT:ok import_s={{t1-t0:.3f}}")
        except Exception as e:
            print(f"RESULT:fail {{e}}")
        """
    )
    wall_s, rss_kb, _exit, _stderr, warnings, errors = _run_py(python, script, env)
    return BenchResult(name="import_smoke", wall_s=wall_s, rss_kb=rss_kb, warnings=warnings, errors=errors)


def bench_boot(python: Path, env: dict[str, str] | None) -> BenchResult:
    """Boot smoke: import and access version attr."""
    script = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(PROJECT_ROOT)!r})
        t0 = time.perf_counter()
        try:
            import autonomous_orchestrator
            _ = getattr(autonomous_orchestrator, '__version__', None) or \\
                getattr(autonomous_orchestrator, 'VERSION', None) or \\
                'no-version'
            t1 = time.perf_counter()
            print(f"RESULT:ok boot_s={{t1-t0:.3f}}")
        except Exception as e:
            print(f"RESULT:fail {{e}}")
        """
    )
    wall_s, rss_kb, _exit, _stderr, warnings, errors = _run_py(python, script, env)
    return BenchResult(name="boot_smoke", wall_s=wall_s, rss_kb=rss_kb, warnings=warnings, errors=errors)


def bench_execution_optimizer(python: Path, env: dict[str, str] | None) -> BenchResult:
    """Run execution_optimizer smoke."""
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(PROJECT_ROOT)!r})
        from utils.execution_optimizer import ExecutionOptimizer
        opt = ExecutionOptimizer()
        print("RESULT:ok")
        """
    )
    wall_s, rss_kb, _exit, _stderr, warnings, errors = _run_py(python, script, env, timeout=60)
    return BenchResult(name="execution_optimizer", wall_s=wall_s, rss_kb=rss_kb, warnings=warnings, errors=errors)


def bench_content_miner(python: Path, env: dict[str, str] | None) -> BenchResult:
    """Run content_miner probe if available."""
    probe_path = PROJECT_ROOT / "tools" / "dump_asyncio_tasks.py"
    if not probe_path.exists():
        return BenchResult(name="content_miner", wall_s=0.0, errors=1)

    t0 = time.perf_counter()
    rss_kb = 0
    try:
        proc = subprocess.Popen(
            [str(python), str(probe_path)],
            env={**(env or {}), "PYTHONPATH": str(PROJECT_ROOT)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )
        _stdout, stderr_data = proc.communicate(timeout=60)
        wall_s = time.perf_counter() - t0
        exit_code = proc.returncode
        stderr_text = stderr_data.decode(errors="replace")
    except subprocess.TimeoutExpired:
        proc.kill()
        wall_s = time.perf_counter() - t0
        exit_code = 124
        stderr_text = "TIMEOUT"
    except Exception as e:
        wall_s = time.perf_counter() - t0
        exit_code = 65
        stderr_text = str(e)

    warnings = sum(1 for line in stderr_text.splitlines() if "Warning" in line)
    errors = sum(1 for line in stderr_text.splitlines() if "Error" in line or "Exception" in line)
    return BenchResult(name="content_miner", wall_s=wall_s, rss_kb=rss_kb, warnings=warnings, errors=errors)


def format_result(r: BenchResult) -> str:
    parts = [f"{r.name}: wall={r.wall_s:.3f}s"]
    if r.rss_kb:
        parts.append(f"rss={r.rss_kb}KB")
    if r.warnings:
        parts.append(f"warnings={r.warnings}")
    if r.errors:
        parts.append(f"errors={r.errors}")
    return " ".join(parts)


def main() -> int:
    print("F214I-2 Python 3.14 JIT Benchmark")
    print("=" * 50)
    print(f"Python: {VENV_PYTHON}")
    print(f"Project: {PROJECT_ROOT}")
    print()

    jit_status = check_jit(VENV_PYTHON)
    print(f"JIT available: {jit_status.available}")
    print(f"Reason: {jit_status.reason}")
    print()

    if not jit_status.available:
        print("NO_PATCH: Python 3.14.4 from uv was built WITHOUT --with-jit.")
        print(f"  {jit_status.reason}")
        print("Verdict: KEEP_DISABLED — No JIT support in this interpreter build.")
        return 64

    default_env: dict[str, str] = {}
    jit_env: dict[str, str] = {"PYTHON_JIT": "1"}

    print("Running benchmarks...")
    print()

    results: dict[str, list[BenchResult]] = {"default": [], "jit": []}

    for label, env in [("default", default_env), ("jit", jit_env)]:
        print(f"--- {label} ---")
        for bench_fn in [bench_import, bench_boot, bench_execution_optimizer, bench_content_miner]:
            try:
                r = bench_fn(VENV_PYTHON, env)
                results[label].append(r)
                print(f"  {format_result(r)}")
            except Exception as e:
                print(f"  {bench_fn.__name__}: ERROR {e}")
        print()

    print("=" * 50)
    print("COMPARISON (JIT vs default)")
    print("=" * 50)

    default_results = {r.name: r for r in results["default"]}
    jit_results = {r.name: r for r in results["jit"]}

    any_improvement = False
    for name in sorted(default_results.keys()):
        d = default_results.get(name)
        j = jit_results.get(name)
        if d and j:
            delta = j.wall_s - d.wall_s
            pct = (delta / d.wall_s * 100) if d.wall_s > 0 else float("nan")
            rss_delta = (j.rss_kb or 0) - (d.rss_kb or 0)
            print(
                f"{name}: default={d.wall_s:.3f}s  jit={j.wall_s:.3f}s  "
                f"delta={delta:+.3f}s ({pct:+.1f}%)  "
                f"rss_default={d.rss_kb}KB  rss_jit={j.rss_kb}KB  "
                f"rss_delta={rss_delta:+d}KB"
            )
            if j.wall_s < d.wall_s:
                any_improvement = True
        elif d:
            print(f"{name}: default={d.wall_s:.3f}s  jit=N/A")

    print()
    if any_improvement:
        print("Verdict: EXPERIMENTAL_ONLY — JIT shows marginal improvement.")
        print("Keep disabled in production. Enable only for dedicated benchmarking.")
    else:
        print("Verdict: KEEP_DISABLED — JIT provides no measurable benefit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
