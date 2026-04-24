"""
Repository Artifact Hygiene Probe — Sprint F201C

Verifies git tracked tree contains no bytecode or ghost backup artifacts.
Ghost audit counts source .py call-sites, not bytecode.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


class TestRepoArtifactHygiene:
    """Probe: no bytecode or ghost backup artifacts in tracked tree."""

    def _git_ls_files(self, *patterns: str) -> list[str]:
        """Return tracked files matching any of the given patterns."""
        cmd = ["git", "ls-files"]
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        result.check_returncode()
        all_files = result.stdout.splitlines()
        matched = []
        for f in all_files:
            for pat in patterns:
                if pat in f or f.endswith(pat):
                    matched.append(f)
                    break
        return matched

    def test_no_tracked_pycache(self):
        """Invariant: __pycache__/ not in git tracked tree."""
        pycache = self._git_ls_files("__pycache__")
        assert len(pycache) == 0, f"tracked __pycache__: {pycache}"

    def test_no_tracked_pyc(self):
        """Invariant: *.pyc not in git tracked tree."""
        pyc = self._git_ls_files(".pyc", ".pyo")
        assert len(pyc) == 0, f"tracked .pyc/.pyo: {pyc}"

    def test_no_tracked_dsvc(self):
        """Invariant: .DS_Store not in git tracked tree."""
        dsvc = self._git_ls_files(".DS_Store")
        assert len(dsvc) == 0, f"tracked .DS_Store: {dsvc}"

    def test_no_tracked_bak_files(self):
        """Invariant: ghost backup source (*.bak*) not in tracked tree."""
        bak = self._git_ls_files(".bak")
        ghost_bak = [f for f in bak if "_bak_" in f or f.endswith(".bak")]
        assert len(ghost_bak) == 0, f"tracked ghost backups: {ghost_bak}"

    def test_no_tracked_srclight_bak(self):
        """Invariant: .srclight_bak/ not in git tracked tree."""
        srclight = self._git_ls_files(".srclight_bak")
        assert len(srclight) == 0, f"tracked srclight_bak: {srclight}"

    def test_no_tracked_probe_venv(self):
        """Invariant: probe test venv not in git tracked tree."""
        venv = self._git_ls_files(".venv_ddgs")
        assert len(venv) == 0, f"tracked probe venv: {venv}"

    def test_gitignore_covers_artifacts(self):
        """Verify .gitignore contains essential artifact patterns."""
        gitignore = REPO_ROOT / ".gitignore"
        assert gitignore.exists(), ".gitignore missing"

        content = gitignore.read_text()
        required = ["__pycache__", "*.pyc", "*.bak", ".srclight"]
        missing = [p for p in required if p not in content]
        assert len(missing) == 0, f".gitignore missing patterns: {missing}"
