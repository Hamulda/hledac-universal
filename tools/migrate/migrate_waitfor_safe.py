"""
P2: asyncio.wait_for → safe_wait_for() migrace.

safe_wait_for() je drop-in náhrada za asyncio.wait_for s korektním
TaskGroup composition (asyncio.timeout pod hoodem).

Pravidla:
- Ponechat: asyncio.wait_for(asyncio.shield(...)) — správný vzor
- Ponechat: test soubory
- Nahradit: všechny ostatní await asyncio.wait_for(coro, timeout=X)
- Import: from hledac.universal.utils.asyncx import safe_wait_for
  (pokud soubor už importuje z async_helpers, přidat jen safe_wait_for)
"""

import re
import sys
from pathlib import Path
from _core import aclose

SRC = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")

# Soubory s asyncio.wait_for (mimo testů a shield patterns)
WAIT_FOR_FILES = [
    "coordinators/performance_coordinator.py",
    "core/sync_bridge.py",
    "brain/_batch/batch_processor.py",
    "coordinators/memory_coordinator.py",
    "brain/ner_engine.py",
    "brain/_cache/warmup.py",
    "brain/_inference/stream_handler.py",
    "brain/_mlx_dispatcher.py",
    "brain/mlxcel_ipc_client.py",
    "core/resource_governor.py",
    "runtime/scheduler_v2/scheduler.py",
    "runtime/scheduler_v2/winddown.py",
    "runtime/protocols/cleanup_protocol.py",
    "transport/http3_lane.py",
    "evidence_log.py",
]

SHIELD_PATTERNS = [
    "asyncio.wait_for(asyncio.shield(",
]

IMPORT_LINE = "from hledac.universal.utils.asyncx import safe_wait_for"
IMPORT_RE = re.compile(r"^from hledac\.universal\.utils\.async_helpers\s+import\s+")

# safe_wait_for import already in these files
ALREADY_IMPORTED = {
    "coordinators/memory_coordinator.py",
    "brain/_cache/warmup.py",
}

def needs_shield(line: str) -> bool:
    return "asyncio.wait_for(asyncio.shield(" in line

def replace_wait_for(content: str) -> tuple[str, int, int]:
    """Nahradí asyncio.wait_for → safe_wait_for, vrací (new_content, n_replaced, n_skipped_shield)."""
    lines = content.splitlines()
    n_replaced = 0
    n_skipped_shield = 0
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if needs_shield(line):
            new_lines.append(line)
            n_skipped_shield += 1
            i += 1
            continue
        # Match: await asyncio.wait_for(...)
        m = re.search(r"await asyncio\.wait_for\(", line)
        if m:
            # Single-line: await asyncio.wait_for(coro, timeout=X)
            new_line = re.sub(
                r"await asyncio\.wait_for\(",
                "await safe_wait_for(",
                line
            )
            # Also fix the timeout= -> timeout= (same arg name)
            new_lines.append(new_line)
            n_replaced += 1
            i += 1
            continue
        new_lines.append(line)
        i += 1
    return "\n".join(new_lines), n_replaced, n_skipped_shield

def add_import(content: str, filepath: str) -> str:
    """Přidá safe_wait_for import pokud chybí."""
    rel = str(Path(filepath).relative_to(SRC))
    if rel in ALREADY_IMPORTED:
        return content
    if IMPORT_RE.search(content):
        # Import exists, ensure safe_wait_for is in it
        m = IMPORT_RE.search(content)
        line_start = content.rfind("\n", 0, m.start()) + 1
        line_end = content.find("\n", m.start())
        old_import = content[line_start:line_end]
        if "safe_wait_for" not in old_import:
            new_import = old_import.rstrip() + ", safe_wait_for"
            content = content[:line_start] + new_import + content[line_end:]
        return content
    # Add new import after docstring or at top after other imports
    # Try to find last import line
    lines = content.splitlines()
    insert_after = -1
    for j, line in enumerate(lines):
        if line.startswith("from ") or line.startswith("import "):
            insert_after = j
    if insert_after >= 0:
        insert_pos = content.find("\n".join(lines[:insert_after+1])) + len("\n".join(lines[:insert_after+1]))
        content = content[:insert_pos] + "\n" + IMPORT_LINE + content[insert_pos:]
    else:
        content = IMPORT_LINE + "\n" + content
    return content

def migrate_file(filepath: Path) -> dict:
    rel = str(filepath.relative_to(SRC))
    try:
        content = filepath.read_text()
    except Exception as e:
        return {"file": rel, "error": str(e)}

    new_content, n_replaced, n_skipped = replace_wait_for(content)
    if n_replaced == 0 and n_skipped == 0:
        return {"file": rel, "status": "no wait_for found"}

    new_content = add_import(new_content, str(filepath))

    filepath.write_text(new_content)
    return {
        "file": rel,
        "replaced": n_replaced,
        "skipped_shield": n_skipped,
        "status": "ok"
    }

def main():
    results = []
    for rel_path in WAIT_FOR_FILES:
        filepath = SRC / rel_path
        if not filepath.exists():
            print(f"SKIP (not found): {rel_path}")
            continue
        res = migrate_file(filepath)
        if res.get("status") == "ok":
            print(f"OK: {rel_path} — {res['replaced']} replaced, {res['skipped_shield']} shield skipped")
        elif res.get("status") == "no wait_for found":
            print(f"NONE: {rel_path}")
        else:
            print(f"ERR: {rel_path}: {res.get('error')}")
        results.append(res)

    total_replaced = sum(r.get("replaced", 0) for r in results)
    total_skipped = sum(r.get("skipped_shield", 0) for r in results)
    print(f"\n=== DONE: {total_replaced} replaced, {total_skipped} shield kept, {len(results)} files processed ===")

if __name__ == "__main__":
    main()
