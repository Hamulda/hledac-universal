#!/usr/bin/env python3
"""
AST-Grep structural search and rewrite wrapper.
Provides JSON output for programmatic consumption.
"""
import sys
import subprocess
import json

def ast_search(pattern: str, language: str, path: str) -> dict:
    cmd = ["ast-grep", "scan", "--pattern", pattern, "--lang", language, "--json", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        try:
            return {"success": True, "results": json.loads(result.stdout)}
        except json.JSONDecodeError:
            return {"success": True, "raw": result.stdout, "stderr": result.stderr}
    except FileNotFoundError:
        return {"success": False, "error": "ast-grep not installed. Install: cargo install ast-grep"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def ast_rewrite(pattern: str, rewrite: str, language: str, path: str) -> dict:
    cmd = ["ast-grep", "scan", "--pattern", pattern, "--rewrite", rewrite, "--lang", language, "-U", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        }
    except FileNotFoundError:
        return {"success": False, "error": "ast-grep not installed. Install: cargo install ast-grep"}
    except Exception as e:
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    if len(sys.argv) < 5:
        print(json.dumps({"error": "Usage: ast_grep.py <search|rewrite> <pattern> <lang> <path> [rewrite]"}))
        sys.exit(1)
    
    mode = sys.argv[1]
    pattern = sys.argv[2]
    language = sys.argv[3]
    path = sys.argv[4]
    
    if mode == "search":
        result = ast_search(pattern, language, path)
    elif mode == "rewrite":
        rewrite = sys.argv[5] if len(sys.argv) > 5 else ""
        result = ast_rewrite(pattern, rewrite, language, path)
    else:
        result = {"error": f"Unknown mode: {mode}"}
    
    print(json.dumps(result, indent=2))