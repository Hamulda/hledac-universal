#!/usr/bin/env python3
"""
CI guard against new configuration flags.
Compares current state with baseline file.
Baseline is stored in hledac/universal/.flags_baseline.json
"""
import ast
import json
import re
import sys
from pathlib import Path

# Root folder = hledac/universal
ROOT = Path(__file__).parent.parent
BASELINE_FILE = ROOT / ".flags_baseline.json"
IGNORE_DIRS = {"tests", "venv", "__pycache__", ".git"}


def extract_module_flags(filepath: Path) -> set[str]:
    """Returns set of module-level variable names matching pattern."""
    with open(filepath, "rb") as f:
        try:
            tree = ast.parse(f.read(), filename=str(filepath))
        except SyntaxError:
            return set()

    flags = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            if not isinstance(t, ast.Name):
                continue
            name = t.id
            # Regex with word boundary
            if re.search(r"(ENABLE_|USE_|FEATURE_|_AVAILABLE\b)", name):
                flags.add(name)
    return flags


def scan_all_flags() -> dict[str, list[str]]:
    """Scans all .py files and returns dict {file: [flags]}."""
    result = {}
    for py_file in ROOT.rglob("*.py"):
        if any(part in IGNORE_DIRS for part in py_file.parts):
            continue
        flags = extract_module_flags(py_file)
        if flags:
            rel_path = str(py_file.relative_to(ROOT))
            result[rel_path] = sorted(flags)
    return result


def main():
    current = scan_all_flags()

    if not current:
        print("❌ No .py files found - something is wrong")
        return 1

    if not BASELINE_FILE.exists():
        BASELINE_FILE.write_text(json.dumps(current, indent=2))
        print(f"✅ Baseline created: {BASELINE_FILE}")
        return 0

    baseline = json.loads(BASELINE_FILE.read_text())
    new_flags = {}
    for file, flags in current.items():
        old_flags = set(baseline.get(file, []))
        new = set(flags) - old_flags
        if new:
            # Add context ±3 lines
            full_path = ROOT / file
            lines = full_path.read_text().splitlines()
            contexts = []
            for flag in sorted(new):
                for i, line in enumerate(lines):
                    if flag in line:
                        start = max(0, i - 3)
                        end = min(len(lines), i + 4)
                        context = "\n".join(lines[start:end])
                        contexts.append(f"    {full_path}:{i+1}\n{context}\n")
                        break
            new_flags[file] = contexts

    if new_flags:
        print("❌ Found new configuration flags (forbidden):")
        for file, contexts in new_flags.items():
            print(f"  {file}:")
            for ctx in contexts:
                print(ctx)
        return 1

    # Special check for orchestrator: flag count must not increase
    orch_file = "autonomous_orchestrator.py"
    orch_current = len(current.get(orch_file, []))
    orch_baseline = len(baseline.get(orch_file, []))
    if orch_current > orch_baseline:
        print(f"❌ In {orch_file} flags increased: baseline={orch_baseline}, now={orch_current}")
        return 1

    print("✅ OK – no new flags, orchestrator OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
