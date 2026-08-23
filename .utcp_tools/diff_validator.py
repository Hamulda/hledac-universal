#!/usr/bin/env python3
"""
Diff-scoped fast validator.
Runs linting/type-checking only on files modified in git diff.
"""
import sys
import subprocess
import json
import os

def get_git_diff_files(extensions=None):
    """Get list of files changed in git diff."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, timeout=30
        )
        files = result.stdout.strip().split("\n")
        if extensions:
            return [f for f in files if any(f.endswith(ext) for ext in extensions)]
        return files
    except Exception as e:
        return []

def validate_python(files):
    """Run ruff check on Python files."""
    if not files:
        return {"python": {"success": True, "checked": 0}}
    cmd = ["ruff", "check"] + files
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return {
            "python": {
                "success": result.returncode == 0,
                "checked": len(files),
                "output": result.stdout + result.stderr
            }
        }
    except FileNotFoundError:
        return {"python": {"error": "ruff not installed"}}
    except Exception as e:
        return {"python": {"error": str(e)}}

def validate_rust(files):
    """Run cargo check on Rust files."""
    if not files:
        return {"rust": {"success": True, "checked": 0}}
    cmd = ["cargo", "check", "--message-format=json"] + files
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return {
            "rust": {
                "success": result.returncode == 0,
                "checked": len(files),
                "output": result.stdout[:5000]
            }
        }
    except FileNotFoundError:
        return {"rust": {"error": "cargo not installed"}}
    except Exception as e:
        return {"rust": {"error": str(e)}}

if __name__ == "__main__":
    scope = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    py_files = get_git_diff_files([".py"]) if scope in ("all", "python") else []
    rs_files = get_git_diff_files([".rs"]) if scope in ("all", "rust") else []
    
    result = {"files_checked": {"python": len(py_files), "rust": len(rs_files)}}
    
    if scope in ("all", "python"):
        result.update(validate_python(py_files))
    if scope in ("all", "rust"):
        result.update(validate_rust(rs_files))
    
    print(json.dumps(result, indent=2))