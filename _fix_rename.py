#!/usr/bin/env python3
"""Bulk rename safe_gather_ok → safe_gather_ok across all Python files."""
import pathlib
import re

ROOT = pathlib.Path(__file__).parent
PATTERN = re.compile(r'\bsafe_gather_dropin\b')
COUNT = 0

for py_file in ROOT.rglob("*.py"):
    if ".venv" in py_file.parts or "pytest_cache" in py_file.parts or "__pycache__" in py_file.parts:
        continue
    try:
        content = py_file.read_text()
    except Exception:
        continue
    if PATTERN.search(content):
        new_content = PATTERN.sub("safe_gather_ok", content)
        py_file.write_text(new_content)
        print(f"RENAMED: {py_file.relative_to(ROOT)}")
        COUNT += 1

print(f"\nTotal: {COUNT} files updated.")
