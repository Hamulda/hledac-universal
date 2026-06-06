# Ensure hledac namespace resolves for all sibling subpackages.
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")

# Prepend the paths needed for `hledac` to be importable. The canonical
# bootstrap below will then extend sys.path with every spec sibling.
# Idempotent — duplicates are silently dropped by `set` membership.
#
# Order matters: REPO_ROOT must end up at sys.path[0] (Python walks the
# path list in order; if the parent of the project is at index 0, Python
# discovers `hledac` as an *implicit namespace package* there and the
# real `hledac/_namespace_bootstrap.py` under REPO_ROOT becomes invisible).
# Tuple order is the iteration order, but `insert(0, …)` reverses it, so
# we list the parent FIRST and REPO_ROOT SECOND to land the desired
# final ordering of [REPO_ROOT, parent, …].
for _p in ('/Users/vojtechhamada/PycharmProjects/Hledac', str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Canonical namespace bootstrap (idempotent, fail-safe).
from hledac._namespace_bootstrap import ensure_namespace_paths
ensure_namespace_paths()


# ── R0 autoprobe (Sprint F26X) ──────────────────────────────────────────────
# Hermetický audit generuje probe_r0_nonfeed_reality_lock/ artifacts, které
# TestNoProductionEdits očekává. Fixture ho spustí LEN když artifacts chybí
# nebo jsou starší než zdroják (mtime check). Tím zajišťujeme:
#   1) Žádný overhead za běhu (lazy)
#   2) Self-healing při změně kódu
#   3) HLEDAC_REGEN_PROBES=1 vynutí rerun (pro CI)


def _r0_artifacts_stale() -> bool:
    """Return True if R0 probe artifacts are missing or older than the runner."""
    probe_dir = REPO_ROOT / "probe_r0_nonfeed_reality_lock"
    runner = REPO_ROOT / "tools" / "probe_r0_nonfeed_reality_lock.py"
    artifacts = (
        probe_dir / "REPORT_NONFEED_REALITY_LOCK.md",
        probe_dir / "nonfeed_reality_lock.json",
    )
    if not all(p.exists() for p in artifacts):
        return True
    if not runner.exists():
        return False  # nic ke srovnání
    runner_mtime = runner.stat().st_mtime
    return any(p.stat().st_mtime < runner_mtime for p in artifacts)


def _ensure_r0_artifacts() -> None:
    """Run R0 probe runner if artifacts are stale (env-gated)."""
    if os.environ.get("HLEDAC_SKIP_AUTOPROBE") == "1":
        return
    if not _r0_artifacts_stale() and os.environ.get("HLEDAC_REGEN_PROBES") != "1":
        return
    runner = REPO_ROOT / "tools" / "probe_r0_nonfeed_reality_lock.py"
    if not runner.exists():
        return
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    try:
        subprocess.run(
            [sys.executable, str(runner)],
            cwd=str(REPO_ROOT),
            env=env,
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        # Fail-safe: neblokuj testy kvůli autoprobe
        pass


_ensure_r0_artifacts()
