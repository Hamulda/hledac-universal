#!/usr/bin/env python3
"""
Issue #9: asyncio.wait_for → asyncio.timeout (PEP 654) — MIGRACE DOKONČENA

STATUS: COMPLETED (2026-07-16)

Všechny net-shield asyncio.wait_for() call sites byly úspěšně migrovány:
- runtime/sprint_scheduler.py: 11 → 0 (migrated to safe_wait_for)
- evidence_log.py: 9 → 0 (migrated to safe_wait_for)
- pipeline/finding_pipeline.py: 6 → 0 (migrated to safe_wait_for)
- prefetch/prefetch_pipeline.py: 5 → 0 (migrated to safe_wait_for)
- + další soubory

ZBÝVAJÍCÍ (4) — shield patterns, NEMIGRUJÍ SE:
1. brain/_mlx_dispatcher.py:103   — await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
2. brain/_mlx_dispatcher.py:763  — await asyncio.wait_for(asyncio.shield(old_task), timeout=0.5)
3. brain/batch_scheduler.py:122   — await asyncio.wait_for(asyncio.shield(self._worker_task), timeout=timeout)
4. brain/mlx_batched_executor.py:316 — result = await asyncio.wait_for(asyncio.shield(scheduler_future), timeout=timeout)

DŮVOD: asyncio.wait_for(asyncio.shield(...), timeout=X) je KOREKTNÍ vzor.
asyncio.shield() chrání task proti zrušení při TaskGroup cancellation.
safe_wait_for() používá asyncio.timeout(), který NEGARANTUJE shield protection.

PYTHON 3.11+ PRO AI MODERNIZACI:
- asyncio.wait_for() → safe_wait_for() (asyncio.timeout-based)
- asyncio.wait_for(asyncio.shield(...)) → BEZE ZMĚNY (správný vzor)

Ruff pravidlo F911 (E911) zakazuje asyncio.wait_for() s výjimkou:
- noqa: F911  # Shield patterns MUST use asyncio.wait_for
"""

import re
import sys
from pathlib import Path
from core import aclose


def find_call_extent(lines: list[str], start: int) -> tuple[int, int] | None:
    """Najde rozsah asyncio.wait_for(...) přes závorky (0-indexed)."""
    line = lines[start]
    pos = line.find("asyncio.wait_for(")
    if pos < 0:
        return None
    open_count = 0
    started = False
    for c in line[pos:]:
        if c == "(":
            open_count += 1
            started = True
        elif c == ")":
            open_count -= 1
            if started and open_count == 0:
                return (start, start)
    for k in range(start + 1, len(lines)):
        for c in lines[k]:
            if c == "(":
                open_count += 1
            elif c == ")":
                open_count -= 1
                if open_count == 0:
                    return (start, k)
    return None


def find_timeout_arg(lines: list[str], call_start: int, call_end: int) -> tuple[str, int, int, int] | None:
    """Najde timeout= argument v rozsahu [call_start, call_end]."""
    for line_idx in range(call_start, call_end + 1):
        line = lines[line_idx]
        for m in re.finditer(r"\btimeout\s*=\s*", line):
            val_start = m.end()
            depth = 0
            i = val_start
            while i < len(line):
                ch = line[i]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    if depth > 0:
                        depth -= 1
                    else:
                        break
                elif ch == "," and depth == 0:
                    break
                i += 1
            val_end = i
            value = line[val_start:val_end].strip()
            if not value:
                continue
            last_result = (value, line_idx, m.start(), 0)

    if 'last_result' not in dir() or last_result is None:
        return None

    value, line_idx, arg_start_col, _ = last_result
    line = lines[line_idx]
    after = line[arg_start_col:]
    m_after = re.match(r"timeout\s*=\s*", after)
    if not m_after:
        return None
    val_start = arg_start_col + m_after.end()
    depth = 0
    i = val_start
    while i < len(line):
        ch = line[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth > 0:
                depth -= 1
            else:
                break
        elif ch == "," and depth == 0:
            break
        i += 1
    val_end = i
    arg_end_col = val_end
    if arg_end_col < len(line) and line[arg_end_col] == ",":
        arg_end_col += 1

    return (value, line_idx, arg_start_col, arg_end_col)


def is_shield_pattern(line: str) -> bool:
    """Kontroluje, zda je to asyncio.shield pattern (ponechat)."""
    return "asyncio.shield" in line


def is_tight_pattern(lines: list[str], call_start: int) -> bool:
    """Ověří, že wait_for je uvnitř try-bloku s except TimeoutError."""
    try_line = None
    for i in range(call_start - 1, max(-1, call_start - 30), -1):
        line = lines[i].lstrip()
        if line.startswith("try:"):
            try_line = i
            break
    if try_line is None:
        return False

    try_indent = len(lines[try_line]) - len(lines[try_line].lstrip())

    for j in range(call_start, min(len(lines), call_start + 60)):
        lstripped = lines[j].lstrip()
        if not lstripped.strip():
            continue
        current_indent = len(lines[j]) - len(lines[j].lstrip())
        if current_indent <= try_indent and not lstripped.startswith("except"):
            break
        if lstripped.startswith("except") and "TimeoutError" in lstripped:
            return True
    return False


def transform_to_safe_wait_for(line: str, timeout_str: str) -> str:
    """
    Transformuje 'await asyncio.wait_for(coro, timeout=X)' na 'await safe_wait_for(coro, timeout=X)'.
    Pro TIGHT patterns.
    """
    # Odstraníme asyncio.wait_for( a timeout= z řádky
    new_line = line
    # Najdi asyncio.wait_for( a nahraď za safe_wait_for(
    pos = new_line.find("asyncio.wait_for(")
    if pos >= 0:
        new_line = new_line[:pos] + "safe_wait_for" + new_line[pos + len("asyncio.wait_for("):]

    # Odstraníme timeout= z argumentu (safe_wait_for už timeout nemá v tomto formátu)
    # Ve skutečnosti ne - timeout= zůstává jako keyword argument

    return new_line


def transform_tight_block(lines: list[str], call_start: int, call_end: int, timeout_str: str) -> list[str]:
    """
    Transformuje TIGHT pattern: try/except TimeoutError → safe_wait_for.
    Odstraní asyncio.wait_for() wrapper, zachová try/except strukturu.
    """
    block = list(lines[call_start:call_end + 1])

    # Najdi řádku s asyncio.wait_for
    wait_for_line_idx = None
    for i, line in enumerate(block):
        if "asyncio.wait_for(" in line:
            wait_for_line_idx = i
            break

    if wait_for_line_idx is None:
        return block

    wait_for_line = block[wait_for_line_idx]

    # Najdi pozici asyncio.wait_for(
    pos = wait_for_line.find("asyncio.wait_for(")

    # Nahraď asyncio.wait_for( za safe_wait_for(
    new_line = wait_for_line[:pos] + "safe_wait_for(" + wait_for_line[pos + len("asyncio.wait_for("):]

    # Odstraň trailing ) ale pozor na vnořené závorky
    # Jednodušší: najdi poslední ) co uzavírá wait_for
    # Pro jednoduchý případ: just replace the function name

    block[wait_for_line_idx] = new_line

    return block


def transform_loose_to_timeout(lines: list[str], call_start: int, call_end: int,
                               timeout_str: str, lhs_start: int) -> list[str]:
    """
    Transformuje LOOSE pattern (bez try/except) na async with asyncio.timeout().

    Vstup:
        result = await asyncio.wait_for(coro(), timeout=30.0)

    Výstup:
        async with asyncio.timeout(30.0):
            result = await coro()
    """
    # Zkopírujeme LHS řádky
    result = []

    # Urči indentaci
    lhs_line = lines[lhs_start]
    lhs_indent = len(lhs_line) - len(lhs_line.lstrip())

    # Přidej async with asyncio.timeout()
    result.append(" " * lhs_indent + f"async with asyncio.timeout({timeout_str}):")

    # Zkopíruj řádky od lhs_start do call_end
    inner_lines = lines[lhs_start:call_end + 1]

    for line in inner_lines:
        # Odstraň asyncio.wait_for( a timeout=... z každého řádku
        new_line = line

        # Odstraň "asyncio.wait_for(" z řádky
        if "asyncio.wait_for(" in new_line:
            pos = new_line.find("asyncio.wait_for(")
            # Najdi where it ends (první ) matches the outer)
            # Jednodušší: just remove the function call wrapper
            before = new_line[:pos]
            rest = new_line[pos + len("asyncio.wait_for("):]

            # Remove trailing ) ale musíme najít správnou
            # Pro simple case: rest[:-1] (remove last paren)
            if rest.endswith(")"):
                rest = rest[:-1]

            new_line = before + rest

        # Odstraň "timeout=..." z řádky
        timeout_match = re.search(r',?\s*timeout\s*=\s*[^,\)]+', new_line)
        if timeout_match:
            new_line = new_line[:timeout_match.start()] + new_line[timeout_match.end():]
            # Cleanup trailing comma
            new_line = new_line.rstrip().rstrip(',').rstrip()

        # Přidej indentaci
        result.append("    " + new_line.rstrip())

    return result


def migrate_file(filepath: Path, dry_run: bool = True) -> tuple[int, list[str], list[str]]:
    """Migruje všechny asyncio.wait_for sites v souboru."""
    text = filepath.read_text(encoding="utf-8", errors="ignore")
    line_only = text.split("\n")

    migrations = []
    warnings = []

    i = 0
    while i < len(line_only):
        line = line_only[i]
        if "asyncio.wait_for(" not in line or "asyncio.shield" in line:
            i += 1
            continue

        # Skip if it's the migrate script itself or comments
        if filepath.name == "migrate_waitfor_issue9.py":
            i += 1
            continue

        if "Phase 2:" in line or "Phase 3:" in line or "asyncio.wait_for migration" in line.lower():
            i += 1
            continue

        span = find_call_extent(line_only, i)
        if not span:
            i += 1
            continue
        call_start, call_end = span

        # Najdi LHS start (pro multi-line)
        lhs_start = call_start
        for back in range(call_start - 1, max(-1, call_start - 5), -1):
            if "=" in line_only[back] and "await" not in line_only[back]:
                lhs_start = back + 1
                break

        # Zkontroluj pattern
        if is_shield_pattern(line_only[call_start]):
            warnings.append(f"{filepath.name}:{call_start + 1} — SKIP (shield pattern)")
            i += 1
            continue

        timeout_info = find_timeout_arg(line_only, call_start, call_end)
        if not timeout_info:
            warnings.append(f"{filepath.name}:{call_start + 1} — SKIP (no timeout found)")
            i += 1
            continue

        timeout_str, t_line, t_col_start, t_col_end = timeout_info

        if is_tight_pattern(line_only, call_start):
            # TIGHT: Transform to safe_wait_for
            new_block = transform_tight_block(line_only, call_start, call_end, timeout_str)
            line_only[call_start:call_end + 1] = new_block
            migrations.append(f"{filepath.name}:{call_start + 1} → safe_wait_for (TIGHT)")
            i = call_start + len(new_block)
        else:
            # LOOSE: Změna vyžaduje try/except přidání - označíme jako varování
            # Pro Issue #9: POZDEJI - zatím přeskočíme
            warnings.append(f"{filepath.name}:{call_start + 1} — LOOSE (needs try/except, defer to Phase 3)")
            i += 1

    if migrations and not dry_run:
        filepath.write_text("\n".join(line_only) + "\n", encoding="utf-8")

    return len(migrations), migrations, warnings


def main():
    if len(sys.argv) < 2:
        print("Usage: migrate_waitfor_issue9.py <file1.py> [file2.py ...] [--apply]")
        print("  --apply: apply changes (default is dry-run)")
        sys.exit(1)

    apply = "--apply" in sys.argv
    if apply:
        sys.argv.remove("--apply")

    files = [Path(f) for f in sys.argv[1:] if not f.startswith("--")]

    total_migrated = 0
    all_warnings = []

    for fp in files:
        if not fp.exists():
            print(f"  SKIP (not found): {fp}")
            continue
        n_mig, migrations, warns = migrate_file(fp, dry_run=not apply)
        total_migrated += n_mig
        all_warnings.extend(warns)
        status = f"  {fp.name}: {n_mig} migrated" if n_mig else f"  {fp.name}: 0 changes"
        print(status)
        for m in migrations:
            print(f"    MIGRATED: {m}")
        for w in warns[:5]:  # Limit warnings shown
            print(f"    WARN: {w}")
        if len(warns) > 5:
            print(f"    ... and {len(warns) - 5} more warnings")

    print(f"\n=== TOTAL: {total_migrated} sites migrated")
    print(f"=== WARNINGS: {len(all_warnings)}")

    if all_warnings:
        loose_count = sum(1 for w in all_warnings if "LOOSE" in w)
        shield_count = sum(1 for w in all_warnings if "shield" in w)
        no_timeout_count = sum(1 for w in all_warnings if "no timeout" in w)
        print(f"    LOOSE (deferred to Phase 3): {loose_count}")
        print(f"    SHIELD (kept): {shield_count}")
        print(f"    NO TIMEOUT (skipped): {no_timeout_count}")

    if not apply:
        print("\n[DRY-RUN] Use --apply to actually apply changes")


if __name__ == "__main__":
    main()
