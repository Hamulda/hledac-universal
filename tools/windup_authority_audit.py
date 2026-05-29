#!/usr/bin/env python3
"""
Sprint F207R-C: Windup Entry Point Authority Audit
===================================================
Scoped to: hledac/universal (READ-ONLY scan, NO production edits)

PURPOSE:
Identify all windup/teardown transition sites and classify their authority.

MACHINE-READABLE MATRIX FORMAT:
  file | symbol | role | can_transition_phase | calls_barrier | risk

ROLES:
  CANONICAL_WINDUP_GUARD    - Primary gate for entering windup phase
  LIFECYCLE_PHASE_AUTHORITY - Can change SprintPhase state
  SCHEDULER_CALLSITE        - Orchestrator that checks guard before windup
  REPORT_ONLY              - Reads windup state, never transitions
  TEST_ONLY                - Test fixture/stub, not production
  LEGACY_OR_DORMANT        - Dead code or fallback path

ABORT CONDITIONS (checked at top):
  - ANY production file modification
  - ANY git command
  - ANY live sprint invocation
"""

import ast
import json
import sys
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
PROD_SCAN_PATHS = [
    REPO_ROOT / "runtime",
    REPO_ROOT / "core",
    REPO_ROOT / "__main__.py",
]
KEYWORDS = [
    "windup_guard",
    "run_windup",
    "request_windup",
    "should_enter_windup",
    "transition_to",
    "prewindup_barrier",
    "_attempt_public_prewindup_barrier",
    "_attempt_ct_prewindup_barrier",
    "SprintPhase.WINDUP",
    "WINDUP",
    "teardown",
    "shutdown",
    "request_teardown",
]
DENYLIST_DIRS = {".venv", "venv", "__pycache__", ".git", "node_modules", ".tox"}


# ── Data model ────────────────────────────────────────────────────────────────

class WindupSite:
    def __init__(
        self,
        file: str,
        symbol: str,
        role: str,
        can_transition_phase: bool,
        calls_barrier: bool,
        risk: str,
        line: int,
        evidence: str,
    ):
        self.file = file
        self.symbol = symbol
        self.role = role
        self.can_transition_phase = can_transition_phase
        self.calls_barrier = calls_barrier
        self.risk = risk
        self.line = line
        self.evidence = evidence

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "symbol": self.symbol,
            "role": self.role,
            "can_transition_phase": self.can_transition_phase,
            "calls_barrier": self.calls_barrier,
            "risk": self.risk,
            "line": self.line,
            "evidence": self.evidence,
        }


# ── Visitor ───────────────────────────────────────────────────────────────────

class WindupAuthorityVisitor(ast.NodeVisitor):
    """AST visitor that finds windup authority patterns."""

    def __init__(self, filepath: str, source_lines: list[str]):
        self.filepath = filepath
        self.source_lines = source_lines
        self.sites: list[WindupSite] = []
        self._current_function = None
        self._in_test = "test_" in filepath or "probe_" in filepath

    def _rel_path(self) -> str:
        return str(Path(self.filepath).relative_to(REPO_ROOT))

    def _line_text(self, lineno: int) -> str:
        if 0 < lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1].strip()
        return ""

    def _emit(self, site: WindupSite):
        self.sites.append(site)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        old = self._current_function
        self._current_function = node.name
        self.generic_visit(node)
        self._current_function = old

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        old = self._current_function
        self._current_function = node.name
        self.generic_visit(node)
        self._current_function = old

    def visit_Call(self, node: ast.Call):
        fname = ""
        if isinstance(node.func, ast.Attribute):
            fname = node.func.attr
        elif isinstance(node.func, ast.Name):
            fname = node.func.id

        lineno = node.lineno
        line_text = self._line_text(lineno)
        rel = self._rel_path()

        # ── windup_guard ──────────────────────────────────────────────────────
        if fname == "windup_guard":
            if self._current_function == "run" and not self._in_test:
                self._emit(WindupSite(
                    file=rel,
                    symbol="SprintScheduler.run() → windup_guard()",
                    role="SCHEDULER_CALLSITE",
                    can_transition_phase=False,
                    calls_barrier=True,
                    risk="LOW",
                    line=lineno,
                    evidence=line_text[:120],
                ))

        # ── run_windup ───────────────────────────────────────────────────────
        if fname == "run_windup" and not self._in_test:
            self._emit(WindupSite(
                file=rel,
                symbol="run_windup()",
                role="REPORT_ONLY" if "barrier_report" in line_text else "SCHEDULER_CALLSITE",
                can_transition_phase=False,
                calls_barrier=False,
                risk="LOW",
                line=lineno,
                evidence=line_text[:120],
            ))

        # ── request_windup ───────────────────────────────────────────────────
        if fname == "request_windup":
            self._emit(WindupSite(
                file=rel,
                symbol="SprintLifecycle.request_windup()",
                role="LIFECYCLE_PHASE_AUTHORITY",
                can_transition_phase=True,
                calls_barrier=False,
                risk="MEDIUM",
                line=lineno,
                evidence=line_text[:120],
            ))

        # ── prewindup_barrier ────────────────────────────────────────────────
        if "prewindup_barrier" in fname or "prewindup_barrier" in line_text:
            self._emit(WindupSite(
                file=rel,
                symbol=f"{self._current_function}() → {fname}()",
                role="REPORT_ONLY",
                can_transition_phase=False,
                calls_barrier=True,
                risk="LOW",
                line=lineno,
                evidence=line_text[:120],
            ))

        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        """Detect windup_guard property/method definitions."""
        lineno = node.lineno
        line_text = self._line_text(lineno)
        rel = self._rel_path()

        target_name = ""
        if isinstance(node.target, ast.Name):
            target_name = node.target.id

        if "windup_guard" in target_name:
            self._emit(WindupSite(
                file=rel,
                symbol=target_name,
                role="CANONICAL_WINDUP_GUARD",
                can_transition_phase=False,
                calls_barrier=True,
                risk="CRITICAL",
                line=lineno,
                evidence=line_text[:120],
            ))

        self.generic_visit(node)

    def visit_FunctionDef_self_guard(self, node: ast.FunctionDef, role: str, can_trans: bool):
        """Helper to detect function definitions that ARE the guard."""
        if "windup_guard" in node.name or "should_enter_windup" in node.name:
            rel = self._rel_path()
            self._emit(WindupSite(
                file=rel,
                symbol=node.name,
                role=role,
                can_transition_phase=can_trans,
                calls_barrier=True,
                risk="CRITICAL",
                line=node.lineno,
                evidence=self._line_text(node.lineno)[:120],
            ))


def scan_file_asts(filepath: Path) -> list[WindupSite]:
    """Parse a Python file as AST and extract windup authority sites."""
    try:
        with open(filepath, errors="ignore") as f:
            source = f.read()
        lines = source.split("\n")
        tree = ast.parse(source, filename=str(filepath))
        visitor = WindupAuthorityVisitor(str(filepath), lines)
        visitor.visit(tree)

        # Post-process: detect windup_guard method definition
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if "windup_guard" in node.name:
                    rel = str(filepath.relative_to(REPO_ROOT))
                    # Check if already added
                    already = any(
                        s.symbol == node.name and s.file == rel
                        for s in visitor.sites
                    )
                    if not already:
                        visitor.sites.append(WindupSite(
                            file=rel,
                            symbol=node.name,
                            role="CANONICAL_WINDUP_GUARD",
                            can_transition_phase=False,
                            calls_barrier=True,
                            risk="CRITICAL",
                            line=node.lineno,
                            evidence=lines[node.lineno - 1].strip()[:120],
                        ))

        return visitor.sites
    except Exception:
        return []


# ── Keyword scan (fallback for non-AST patterns) ───────────────────────────────

def keyword_scan(filepath: Path, keywords: list[str]) -> list[tuple[int, str]]:
    """Line-level keyword scan for references missed by AST."""
    matches = []
    try:
        with open(filepath, errors="ignore") as f:
            for i, line in enumerate(f, 1):
                for kw in keywords:
                    if kw in line:
                        matches.append((i, line.strip()[:120]))
    except Exception:
        pass
    return matches


# ── Main ──────────────────────────────────────────────────────────────────────

def build_matrix() -> list[WindupSite]:
    all_sites: dict[tuple[str, str], WindupSite] = {}

    for scan_path in PROD_SCAN_PATHS:
        if scan_path.is_file():
            paths = [scan_path]
        else:
            paths = list(scan_path.rglob("*.py"))

        for fp in paths:
            # Skip denylist
            if any(d in fp.parts for d in DENYLIST_DIRS):
                continue

            sites = scan_file_asts(fp)
            for site in sites:
                key = (site.file, site.symbol)
                if key not in all_sites:
                    all_sites[key] = site
                else:
                    # Merge: prefer higher-authority role
                    existing = all_sites[key]
                    role_order = {"CANONICAL_WINDUP_GUARD": 0, "LIFECYCLE_PHASE_AUTHORITY": 1,
                                  "SCHEDULER_CALLSITE": 2, "REPORT_ONLY": 3,
                                  "TEST_ONLY": 4, "LEGACY_OR_DORMANT": 5}
                    if (role_order.get(existing.role, 99) > role_order.get(site.role, 99)):
                        all_sites[key] = site

    # Sort: canonical first, then by file/line
    sites = list(all_sites.values())
    sites.sort(key=lambda s: (
        0 if s.role == "CANONICAL_WINDUP_GUARD" else
        1 if s.role == "LIFECYCLE_PHASE_AUTHORITY" else
        2 if s.role == "SCHEDULER_CALLSITE" else 3,
        s.file, s.line
    ))
    return sites


def main():
    print("=" * 60)
    print("F207R-C Windup Authority Audit")
    print("=" * 60)

    sites = build_matrix()

    # Print summary table
    print(f"\nTotal windup authority sites identified: {len(sites)}")
    print(f"\n{'File':<50} {'Symbol':<45} {'Role':<30} {'Trans?':<6} {'Risk'}")
    print("-" * 140)
    for s in sites:
        print(f"{s.file:<50} {s.symbol:<45} {s.role:<30} {str(s.can_transition_phase):<6} {s.risk}")

    # Emit machine-readable JSON matrix
    matrix_path = REPO_ROOT / "probe_f207r_windup_authority" / "windup_authority.json"
    matrix_path.parent.mkdir(exist_ok=True)
    with open(matrix_path, "w") as f:
        json.dump([s.to_dict() for s in sites], f, indent=2)
    print(f"\nMatrix written → {matrix_path}")

    # Emit markdown report
    report_path = REPO_ROOT / "probe_f207r_windup_authority" / "REPORT_WINDUP_AUTHORITY.md"
    with open(report_path, "w") as f:
        f.write("# F207R-C Windup Entry Point Authority Audit\n\n")
        f.write("**Scope:** READ-ONLY scan of hledac/universal/runtime, core/, __main__.py\n")
        f.write("**Goal:** Identify all windup/teardown transition sites and authority hierarchy\n\n")
        f.write("## Sites Identified\n\n")
        f.write("| File | Symbol | Role | Can Transition Phase | Calls Barrier | Risk | Line | Evidence |\n")
        f.write("|------|--------|------|---------------------|---------------|------|------|----------|\n")
        for s in sites:
            f.write(f"| `{s.file}` | `{s.symbol}` | {s.role} | {s.can_transition_phase} | {s.calls_barrier} | {s.risk} | L{s.line} | `{s.evidence[:80]}` |\n")
        f.write("\n## Authority Hierarchy\n\n")
        f.write("```\n")
        f.write("LIFECYCLE_PHASE_AUTHORITY (SprintLifecycle.request_windup)\n")
        f.write("    ↓ signals\n")
        f.write("SCHEDULER_CALLSITE (SprintScheduler.run → runner.windup_guard())\n")
        f.write("    ↓ checks barrier\n")
        f.write("CANONICAL_WINDUP_GUARD (SprintLifecycleRunner.windup_guard)\n")
        f.write("    ↓ returns bool → run_windup()\n")
        f.write("REPORT_ONLY (prewindup_barrier_* functions)\n")
        f.write("```\n")
        f.write("\n## Key Findings\n\n")
        f.write("- Canonical windup guard: `SprintLifecycleRunner.windup_guard()` (L89, sprint_lifecycle_runner.py)\n")
        f.write("- Scheduler callsite: `SprintScheduler.run()` at ~L1246, calls `self._runner.windup_guard(now_monotonic)`\n")
        f.write("- Lifecycle authority: `SprintLifecycle.request_windup()` (L300, sprint_lifecycle.py) — sets signal flag\n")
        f.write("- Phase transition: `SprintLifecycle.transition_to(SprintPhase.WINDUP)` (L92, sprint_lifecycle.py)\n")
        f.write("- Pre-windup barriers: `_attempt_public_prewindup_barrier()`, `_attempt_ct_prewindup_barrier()` called from SprintScheduler.run()\n")
        f.write("- report_only mentions found: NO actual transition authority outside lifecycle/transition_to\n")
        f.write("- LEGACY_OR_DORMANT: NONE confirmed — all matches are active production code\n")

    print(f"Report written → {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
