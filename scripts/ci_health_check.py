"""CI health check - validates core imports pass."""
import subprocess
import sys
from pathlib import Path


def check_import(module_path: str, name: str) -> bool:
    """Check a single core import using uv run."""
    code = f"from {module_path} import {name.split('.')[-1]}; print('{name} OK')"
    result = subprocess.run(
        ["uv", "run", "python", "-c", code],
        cwd="/Users/vojtechhamada/PycharmProjects/Hledac",
        capture_output=True,
        text=True,
    )
    # Filter warnings from output
    stderr_lines = [l for l in result.stderr.split('\n')
                   if l and not l.startswith('WARNING:') and not l.startswith('UserWarning')]
    stderr = '\n'.join(stderr_lines).strip()

    if result.returncode != 0:
        print(f"FAIL: {name}")
        if stderr:
            print(f"  stderr: {stderr}")
        return False
    print(f"OK: {name}")
    return True


def main():
    """Run all CI health checks."""
    checks = [
        ("hledac.universal.runtime.sprint_scheduler", "SprintScheduler"),
        ("hledac.universal.knowledge.duckdb_store", "DuckDBShadowStore"),
        ("hledac.universal.coordinators.fetch_coordinator", "FetchCoordinator"),
    ]

    results = [check_import(mod, name) for mod, name in checks]

    if all(results):
        print("\nAll CI health checks passed.")
        return 0
    else:
        print("\nCI health checks FAILED.")
        return 1


if __name__ == "__main__":
    sys.exit(main())