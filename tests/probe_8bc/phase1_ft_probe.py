#!/usr/bin/env python3
"""
Sprint 8BC Phase 1 — Free-Threaded Python 3.13 Readiness Probe
===============================================================
Hard pre-flight check for Python 3.13t / no-GIL on M1.

This script is PROBE-ONLY. It does NOT mutate production code.
It creates temporary venvs under .phase1_probe_8bc/ inside the repo root.

Classification outcomes:
  READY_FOR_PHASE_1
  READY_BUT_PERF_REGRESSION
  BLOCKED_BY_TOOLCHAIN
  BLOCKED_BY_EXTENSION_STACK
  BLOCKED_BY_ORCHESTRATOR_IMPORT
"""


import json
import os
import statistics
import subprocess
import sys
import shutil
from pathlib import Path

# =============================================================================
# CONSTANTS
# =============================================================================

REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
PROBE_DIR = REPO_ROOT / "tests" / "probe_8bc"
VENV_ROOT = REPO_ROOT / ".phase1_probe_8bc"

PACKAGE_MATRIX = [
    ("msgspec", "import msgspec"),
    ("duckdb", "import duckdb"),
    ("ahocorasick", "import ahocorasick; print(ahocorasick.__version__)"),
    ("lmdb", "import lmdb; print(lmdb.__version__)"),
    ("curl_cffi", "import curl_cffi; print(curl_cffi.__version__)"),
    ("mlx", "import mlx; print(mlx.__version__)"),
    ("orchestrator", "import hledac.universal.autonomous_orchestrator"),
]

BASELINE_RUNS = 5
ORCHESTRATOR_TIMEOUT = 60  # seconds per import attempt

# =============================================================================
# CLASSIFICATION CODES
# =============================================================================

TOOLCHAIN_MISSING = "TOOLCHAIN_MISSING"
NOT_INSTALLED = "NOT_INSTALLED"
IMPORT_FAILED_INCOMPATIBLE = "IMPORT_FAILED_INCOMPATIBLE"
GIL_FORCED = "GIL_FORCED"
GIL_STAYS_OFF = "GIL_STAYS_OFF"
GIL_ALREADY_ON = "GIL_ALREADY_ON"
IMPORT_SUCCESS = "IMPORT_SUCCESS"


# =============================================================================
# UTILITIES
# =============================================================================

def run_subprocess(cmd: list[str], timeout: int = 30) -> dict:
    """Run a command in a fresh subprocess, capture everything."""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "returncode": r.returncode,
            "stdout": r.stdout,
            "stderr": r.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "TIMEOUT"}
    except FileNotFoundError:
        return {"returncode": -2, "stdout": "", "stderr": "NOT_FOUND"}


def check_gil_state(python_exe: str) -> dict:
    """Probe GIL state of an interpreter (fresh subprocess)."""
    code = """import sys, sysconfig
gil_fn = getattr(sys, "_is_gil_enabled", None)
print("GIL_ENABLED=" + str(gil_fn() if gil_fn else "NOT_SUPPORTED"), file=sys.stderr)
print("PY_GIL_DISABLED=" + str(sysconfig.get_config_var("Py_GIL_DISABLED")), file=sys.stderr)
print("VERSION=" + sys.version.split()[0], file=sys.stderr)
"""
    result = run_subprocess([python_exe, "-c", code])
    out = result.get("stderr", "")
    lines = out.strip().split("\n")
    gil_enabled = "UNKNOWN"
    py_gil_disabled = "UNKNOWN"
    version = "UNKNOWN"
    for line in lines:
        if line.startswith("GIL_ENABLED="):
            gil_enabled = line.split("=", 1)[1]
        elif line.startswith("PY_GIL_DISABLED="):
            py_gil_disabled = line.split("=", 1)[1]
        elif line.startswith("VERSION="):
            version = line.split("=", 1)[1]
    return {"gil_enabled": gil_enabled, "py_gil_disabled": py_gil_disabled, "version": version}


def probe_package(python_exe: str, pkg_name: str, import_code: str) -> dict:
    """Probe a single package import in a fresh subprocess."""
    # First check GIL state before import
    gil_before = check_gil_state(python_exe)

    # Now attempt import
    code = f"""import sys, sysconfig, psutil, os
gil_fn = getattr(sys, "_is_gil_enabled", None)
before_gil = gil_fn() if gil_fn else "NOT_SUPPORTED"
p = psutil.Process(os.getpid())
rss_before = p.memory_info().rss // (1024*1024)
try:
    {import_code}
    after_gil = gil_fn() if gil_fn else "NOT_SUPPORTED"
    rss_after = p.memory_info().rss // (1024*1024)
    print(f"RESULT=SUCCESS|GIL_BEFORE={{before_gil}}|GIL_AFTER={{after_gil}}|RSS_BEFORE={{rss_before}}|RSS_AFTER={{rss_after}}", file=sys.stderr)
except ImportError as e:
    print(f"RESULT=IMPORT_ERROR|GIL_BEFORE={{before_gil}}|ERROR={{e}}", file=sys.stderr)
except Exception as e:
    print(f"RESULT=ERROR|GIL_BEFORE={{before_gil}}|ERROR={{e}}", file=sys.stderr)
"""
    result = run_subprocess([python_exe, "-c", code], timeout=ORCHESTRATOR_TIMEOUT)
    stderr = result.get("stderr", "")

    # Parse result
    status = IMPORT_SUCCESS
    gil_after = "UNKNOWN"
    rss_before = None
    rss_after = None
    error_msg = ""

    for line in stderr.strip().split("\n"):
        if line.startswith("RESULT="):
            parts = line.split("|")
            result_type = parts[0].split("=", 1)[1]
            for part in parts[1:]:
                if part.startswith("GIL_AFTER="):
                    gil_after = part.split("=", 1)[1]
                elif part.startswith("RSS_BEFORE="):
                    rss_before = int(part.split("=", 1)[1])
                elif part.startswith("RSS_AFTER="):
                    rss_after = int(part.split("=", 1)[1])
                elif part.startswith("ERROR="):
                    error_msg = part.split("=", 1)[1]

            if result_type == "IMPORT_ERROR":
                status = NOT_INSTALLED if "ModuleNotFoundError" in error_msg else IMPORT_FAILED_INCOMPATIBLE
            elif result_type == "ERROR":
                status = IMPORT_FAILED_INCOMPATIBLE

    # Determine GIL classification
    if gil_before.get("gil_enabled") == "True" and gil_after == "True":
        gil_classification = GIL_ALREADY_ON
    elif gil_before.get("gil_enabled") == "False" and gil_after == "True":
        gil_classification = GIL_FORCED
    elif gil_before.get("gil_enabled") == "False" and gil_after in ("False", "NOT_SUPPORTED"):
        gil_classification = GIL_STAYS_OFF
    else:
        gil_classification = "UNKNOWN"

    # Collect warning lines
    warnings = [l for l in stderr.split("\n") if any(k in l for k in ["WARNING", "GIL", "thread", "free-thread"])]

    return {
        "package": pkg_name,
        "python_exe": python_exe,
        "status": status,
        "gil_before": gil_before,
        "gil_after": gil_after,
        "gil_classification": gil_classification,
        "rss_before_mb": rss_before,
        "rss_after_mb": rss_after,
        "warnings": warnings[:5],
        "stderr_snippet": stderr[-500:],
        "returncode": result.get("returncode", -999),
    }


def measure_import_time(python_exe: str, runs: int = 5) -> dict:
    """Measure orchestrator import time with statistics."""
    vals = []
    rss_samples = []
    errors = []

    for i in range(runs):
        code = """import time, psutil, os, sys as _sys
p = psutil.Process(os.getpid())
rss0 = p.memory_info().rss // (1024*1024)
t = time.perf_counter()
import hledac.universal.autonomous_orchestrator
dt = time.perf_counter()-t
rss1 = p.memory_info().rss // (1024*1024)
_sys.stderr.write(f"TS:{dt:.6f}|{rss0}|{rss1}\\n")
_sys.stderr.flush()
"""
        result = run_subprocess([python_exe, "-c", code], timeout=ORCHESTRATOR_TIMEOUT)
        stderr = result.get("stderr", "")
        lines = stderr.strip().split("\n")
        last = lines[-1] if lines else ""

        if last.startswith("TS:"):
            parts = last.replace("TS:", "").split("|")
            try:
                vals.append(float(parts[0]))
                rss_samples.append((int(parts[1]), int(parts[2])))
            except (ValueError, IndexError) as e:
                errors.append(f"run {i}: parse error {e}, last={repr(last[:100])}")
        else:
            errors.append(f"run {i}: no timing line, last={repr(last[:100])}")

    if not vals:
        return {"error": "; ".join(errors), "runs": runs, "success_count": 0}

    return {
        "runs": runs,
        "success_count": len(vals),
        "timing_values": vals,
        "median": statistics.median(vals),
        "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
        "min": min(vals),
        "max": max(vals),
        "rss_samples_mb": rss_samples,
        "errors": errors,
    }


def find_interpreters() -> dict:
    """Find all relevant Python interpreters on the system."""
    interpreters = {}

    # Check python3.13t explicitly
    for name in ["python3.13t", "python3.13t-shared", "python3.13t-nogil"]:
        r = run_subprocess(["/usr/bin/which", name])
        if r.get("returncode") == 0 and r.get("stdout"):
            path = r["stdout"].strip()
            interpreters[name] = {"path": path, "checked": True, "exists": True}
        else:
            interpreters[name] = {"path": None, "checked": True, "exists": False}

    # Check pyenv versions
    r = run_subprocess(["pyenv", "versions", "--bare"])
    if r.get("returncode") == 0:
        versions = [v.strip() for v in r["stdout"].strip().split("\n") if v.strip()]
        for ver in versions:
            if ver.startswith("3.13") or ver.startswith("3.14"):
                key = f"pyenv-{ver}"
                ver_path = f"{os.path.expanduser('~/.pyenv/versions/' + ver)}/bin/python"
                exists = os.path.exists(ver_path)
                interpreters[key] = {
                    "path": ver_path if exists else None,
                    "version": ver,
                    "exists": exists,
                }

    # Current interpreter
    interpreters["current"] = {
        "path": sys.executable,
        "version": sys.version.split()[0],
        "exists": True,
    }

    return interpreters


# =============================================================================
# MAIN PROBE
# =============================================================================

def main():
    report = {
        "probe_version": "8BC-Phase1",
        "probe_script": __file__,
    }

    # STEP 0: Toolchain gate
    print("=" * 70, file=sys.stderr)
    print("STEP 0: TOOLCHAIN GATE", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    interpreters = find_interpreters()
    report["interpreters"] = interpreters

    # Probe each interpreter for GIL state
    ft_interpreters = {}
    for name, info in interpreters.items():
        if info.get("path") and info.get("exists"):
            gil = check_gil_state(info["path"])
            info["gil_probe"] = gil
            if gil.get("py_gil_disabled") == "1" or gil.get("gil_enabled") == "False":
                ft_interpreters[name] = info

    report["ft_interpreters"] = list(ft_interpreters.keys())
    print(f"Free-threaded candidates: {list(ft_interpreters.keys())}", file=sys.stderr)

    # STEP 1: Establish 3.12 baseline
    print("\n" + "=" * 70, file=sys.stderr)
    print("STEP 1: 3.12 BASELINE (5 runs)", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    current_exe = sys.executable
    baseline = measure_import_time(current_exe, runs=BASELINE_RUNS)
    report["baseline_312"] = {
        "python": sys.version.split()[0],
        "executable": current_exe,
        **baseline,
    }
    print(f"3.12 median: {baseline.get('median', 'N/A'):.3f}s, stdev: {baseline.get('stdev', 'N/A'):.3f}s", file=sys.stderr)

    # STEP 2: 3.13t package matrix (only if available)
    print("\n" + "=" * 70, file=sys.stderr)
    print("STEP 2: 3.13t PACKAGE MATRIX", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    if not ft_interpreters:
        report["classification"] = "BLOCKED_BY_TOOLCHAIN"
        report["install_guidance"] = (
            "No free-threaded Python 3.13 interpreter found. "
            "Python 3.13.0 in pyenv is NOT free-threaded (Py_GIL_DISABLED=0). "
            "To install using pyenv:\n"
            "  CONFIGURE_OPTS=--disable-gil pyenv install -v 3.13.0\n"
            "Or use the official Python 3.13 free-threaded macOS installer:\n"
            "  https://www.python.org/ftp/python/3.13.0/"
        )
        print("BLOCKED_BY_TOOLCHAIN: No free-threaded interpreter found.", file=sys.stderr)
        print(report["install_guidance"], file=sys.stderr)
        print(json.dumps(report, indent=2))
        return report

    # Use first available free-threaded interpreter
    ft_name = list(ft_interpreters.keys())[0]
    ft_exe = ft_interpreters[ft_name]["path"]
    print(f"Using free-threaded interpreter: {ft_name} at {ft_exe}", file=sys.stderr)

    # Create temporary venv for 3.13t
    venv_path = VENV_ROOT / f"venv_{ft_name.replace('-', '_').replace('.', '_')}"
    venv_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Creating temp venv at {venv_path}", file=sys.stderr)
    venv_r = run_subprocess([sys.executable, "-m", "venv", str(venv_path)], timeout=60)
    if venv_r.get("returncode") != 0:
        report["classification"] = "BLOCKED_BY_EXTENSION_STACK"
        report["venv_creation_failed"] = venv_r.get("stderr", "")[:500]
        print(f"FAILED to create venv: {venv_r.get('stderr', '')[:200]}", file=sys.stderr)
        print(json.dumps(report, indent=2))
        return report

    venv_python = str(venv_path / "bin" / "python")

    # Upgrade pip
    print("Upgrading pip in 3.13t venv...", file=sys.stderr)
    pip_r = run_subprocess([venv_python, "-m", "pip", "install", "-U", "pip>=24.1"], timeout=120)
    if pip_r.get("returncode") != 0:
        report["classification"] = "BLOCKED_BY_EXTENSION_STACK"
        report["pip_upgrade_failed"] = pip_r.get("stderr", "")[:500]
        print(json.dumps(report, indent=2))
        return report

    # Install packages one by one, track install vs import failures
    package_results = []
    installed_packages = []

    for pkg_name, import_code in PACKAGE_MATRIX:
        print(f"  Probing {pkg_name}...", file=sys.stderr, end=" ", flush=True)
        install_r = run_subprocess(
            [venv_python, "-m", "pip", "install", pkg_name, "--quiet"],
            timeout=300,
        )
        if install_r.get("returncode") != 0:
            print(f"INSTALL_FAIL", file=sys.stderr)
            package_results.append({
                "package": pkg_name,
                "stage": "install",
                "status": "INSTALL_FAILED",
                "stderr": install_r.get("stderr", "")[:300],
            })
            continue

        installed_packages.append(pkg_name)
        print(f"installed, probing import...", file=sys.stderr, end=" ", flush=True)

        # Probe the import in a FRESH subprocess
        probe_result = probe_package(venv_python, pkg_name, import_code)
        print(f"{probe_result['status']}", file=sys.stderr)
        package_results.append(probe_result)

    report["package_matrix"] = package_results
    report["installed_packages"] = installed_packages

    # STEP 3: Orchestrator import timing on 3.13t
    print("\n" + "=" * 70, file=sys.stderr)
    print("STEP 3: ORCHESTRATOR IMPORT TIMING (3.13t)", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    can_import_orchestrator = all(
        r["status"] in (IMPORT_SUCCESS, GIL_ALREADY_ON, GIL_STAYS_OFF)
        for r in package_results
        if r["package"] != "orchestrator"
    )

    baseline_median = baseline.get("median", 999)
    ft_median = None

    if can_import_orchestrator:
        ft_timing = measure_import_time(ft_exe, runs=BASELINE_RUNS)
        report["timing_313t"] = ft_timing
        ft_median = ft_timing.get("median")
        print(f"3.13t median: {ft_median:.3f}s, stdev: {ft_timing.get('stdev', 'N/A'):.3f}s", file=sys.stderr)

        slowdown = ft_median - baseline_median
        report["performance_comparison"] = {
            "baseline_312_median_s": baseline_median,
            "ft_313t_median_s": ft_median,
            "slowdown_s": slowdown,
        }
    else:
        failed = [r["package"] for r in package_results if r["status"] not in (IMPORT_SUCCESS, GIL_ALREADY_ON, GIL_STAYS_OFF)]
        report["orchestrator_blocked_by"] = failed
        print(f"Cannot import orchestrator. Blocking packages: {failed}", file=sys.stderr)

    # STEP 4: GIL stability check
    print("\n" + "=" * 70, file=sys.stderr)
    print("STEP 4: GIL STABILITY ANALYSIS", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    gil_forced_packages = [
        r["package"] for r in package_results
        if r["gil_classification"] == GIL_FORCED
    ]
    report["gil_forced_by_packages"] = gil_forced_packages
    print(f"Packages that forced GIL on: {gil_forced_packages}", file=sys.stderr)

    # STEP 5: Final classification
    print("\n" + "=" * 70, file=sys.stderr)
    print("STEP 5: FINAL CLASSIFICATION", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    if not ft_interpreters:
        classification = "BLOCKED_BY_TOOLCHAIN"
    elif gil_forced_packages:
        classification = "BLOCKED_BY_EXTENSION_STACK"
    elif not can_import_orchestrator:
        classification = "BLOCKED_BY_ORCHESTRATOR_IMPORT"
    elif ft_median is not None:
        slowdown = ft_median - baseline_median
        if slowdown > 0.3:
            classification = "READY_BUT_PERF_REGRESSION"
        else:
            classification = "READY_FOR_PHASE_1"
    else:
        classification = "READY_FOR_PHASE_1"

    report["classification"] = classification

    if classification == "READY_BUT_PERF_REGRESSION":
        slowdown = ft_median - baseline_median
        report["next_step"] = (
            f"3.13t is technically viable but orchestrator import is "
            f"{slowdown:.3f}s slower. Not a blocker but must be tracked. "
            "Proceed to Phase 1 with perf monitoring."
        )
    elif classification == "BLOCKED_BY_TOOLCHAIN":
        report["next_step"] = (
            "Install free-threaded Python: "
            "CONFIGURE_OPTS=--disable-gil pyenv install -v 3.13.0"
        )
    elif classification == "BLOCKED_BY_EXTENSION_STACK":
        report["next_step"] = (
            f"Extension(s) {gil_forced_packages} force GIL. "
            "Find alternatives or wait for free-threaded compatible releases."
        )
    elif classification == "BLOCKED_BY_ORCHESTRATOR_IMPORT":
        report["next_step"] = (
            f"Orchestrator import blocked by: {report.get('orchestrator_blocked_by', [])}. "
            "Fix dependency issues before Phase 1."
        )
    else:
        report["next_step"] = "All checks passed. Proceed to Sprint 8BC Phase 2."

    print(f"CLASSIFICATION: {classification}", file=sys.stderr)
    print(f"Next step: {report.get('next_step', 'N/A')}", file=sys.stderr)

    # Clean up venv
    if venv_path.exists():
        shutil.rmtree(venv_path, ignore_errors=True)

    print("\n" + "=" * 70, file=sys.stderr)
    print("FULL REPORT JSON:", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
