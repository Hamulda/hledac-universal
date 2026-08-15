"""
Phase 2: TIGHT asyncio.wait_for → asyncio.timeout migration.

Bezpečná transformace pro try: ... except TimeoutError: patterny.

Přístup:
1. Najde přesný rozsah asyncio.wait_for(...) (track závorky)
2. Najde LHS span (walk back tracking [] balance)
3. Najde timeout= argument
4. Nahradí celý blok [lhs_start, call_end] novým blokem:
   - async with asyncio.timeout(X):  (nový řádek, indent=LHS)
   - <LHS_start indented +4>
   - <LHS_middle lines indented +4>
   - <LHS_end await <inner> indented +4>  (bez asyncio.wait_for( a ))
   - <inner continuation lines indented +4>
"""

import re
import sys
from pathlib import Path
from _core import aclose


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


def find_lhs_span(lines: list[str], call_start: int) -> tuple[int, int, int]:
    """
    Najde LHS rozsah. Vrací (lhs_start, lhs_end_inclusive, end_col_on_lhs_start).

    Pro 'var = await asyncio.wait_for(' → lhs_start == call_start, end_col je pozice ' ='
    Pro 'results: dict[\n  ...\n] = await asyncio.wait_for(' → lhs_start je řádek s 'results: dict['
    """
    # Nejdřív najdi řádek s '= await asyncio.wait_for(' (může být call_start nebo o 1 výš u jednořádkových)
    await_line = call_start
    while await_line >= 0 and "= await asyncio.wait_for(" not in lines[await_line]:
        await_line -= 1
    if await_line < 0:
        return (call_start, call_start, -1)

    # Najdi pozici '= ' na tomto řádku
    eq_match = re.search(r"=\s*await\s+asyncio\.wait_for\(", lines[await_line])
    if not eq_match:
        return (call_start, call_start, -1)
    eq_pos = eq_match.start()

    # Prefix na tomto řádku (před '= ')
    prefix = lines[await_line][:eq_pos].rstrip()

    if not prefix.endswith("]"):
        # Single-line LHS: 'var = ...'
        return (await_line, await_line, eq_pos)

    # Multi-line LHS: walk back tracking [] balance
    depth = prefix.count("]") - prefix.count("[")
    lhs_start = await_line
    for back in range(await_line - 1, max(-1, await_line - 10), -1):
        line = lines[back]
        # Process line right-to-left
        for ch in reversed(line):
            if ch == "]":
                depth += 1
            elif ch == "[":
                depth -= 1
                if depth == 0:
                    lhs_start = back
                    break
        if depth == 0:
            lhs_start = back
            break
    return (lhs_start, await_line, eq_pos)


def find_timeout_arg(lines: list[str], call_start: int, call_end: int) -> tuple[str, int, int, int] | None:
    """
    Najde timeout= argument v rozsahu [call_start, call_end].
    Vrací (timeout_str, line_idx, col_start, col_end) kde (col_start, col_end) jsou
    column range v rámci linky line_idx, včetně případné čárky.
    Trackuje závorky v hodnotě (např. 'min(10.0, x)').
    """
    # Procházíme řádky, hledáme 'timeout=' a trackujeme závorky
    for line_idx in range(call_start, call_end + 1):
        line = lines[line_idx]
        # Najdi 'timeout=' v této řádce
        for m in re.finditer(r"\btimeout\s*=\s*", line):
            val_start = m.end()
            # Track závorky v hodnotě (až do konce řádky nebo čárky)
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
            # Toto je kandidát; chceme POSLEDNÍ v celém rozsahu
            # (timeout= klíčové slovo, NE jako klíč uvnitř kwargs)
            # Pro jednoduchost: ber poslední
            # Vrátíme po konci smyčky
            # Ale: musíme ověřit, že je to na správné úrovni závorek (vnější wait_for)
            # Pokud je uvnitř jiného volání, přeskočíme
            last_result = (value, line_idx, m.start(), 0)

    if 'last_result' not in dir() or last_result is None:
        return None

    value, line_idx, arg_start_col, _ = last_result
    line = lines[line_idx]

    # Najdi konec argumentu (včetně čárky) na stejné řádce
    # Z arg_start_col hledáme timeout= + value + případná čárka
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
    # Rozšíření o čárku
    arg_end_col = val_end
    if arg_end_col < len(line) and line[arg_end_col] == ",":
        arg_end_col += 1
        # Mezery za čárkou (pokud je to samostatný řádek jako '    timeout=X,\n', necháme to)

    return (value, line_idx, arg_start_col, arg_end_col)


def is_in_try_with_timeout(lines: list[str], call_start: int) -> bool:
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

    # Hledáme except TimeoutError směrem dolů
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


def transform_block(lines: list[str], lhs_start: int, call_start: int, call_end: int,
                    timeout_str: str, t_line: int, t_col_start: int, t_col_end: int) -> list[str]:
    """
    Sestaví nový blok řádků.
    Vstupní rozsah: [lhs_start, call_end].
    Výstup: seznam nových řádků.
    """
    # Zkopírujeme relevantní řádky
    block = list(lines[lhs_start:call_end + 1])

    # 1. Vyčistíme timeout= argument
    if t_line == call_start:
        # timeout= je na stejném řádku jako wait_for — vyčistíme inline
        rel = t_line - lhs_start
        block[rel] = block[rel][:t_col_start] + block[rel][t_col_end:]
    else:
        rel = t_line - lhs_start
        if block[rel].strip() == "" or block[rel].strip().endswith(","):
            # Samostatný řádek, odstraníme celý
            block[rel] = ""
        else:
            # inline na jiném řádku
            block[rel] = block[rel][:t_col_start] + block[rel][t_col_end:]

    # 2. Odstraníme "asyncio.wait_for(" z call_start
    rel = call_start - lhs_start
    line = block[rel]
    pos = line.find("asyncio.wait_for(")
    if pos >= 0:
        # Pokud je '= await' před tím na stejném řádku, vše před necháme; wait_for prefix odstraníme
        block[rel] = line[:pos] + line[pos + len("asyncio.wait_for("):]
        # Strip trailing comma (pokud je await na stejném řádku jako inner)
        # Např. "peers = await node.get_peers(ih_hex)," — odstraníme čárku
        stripped = block[rel].rstrip()
        if stripped.endswith(","):
            block[rel] = stripped[:-1].rstrip()

    # 2b. Sloučení: pokud call_start != call_end, await je na řádku call_start
    # a inner začíná na call_start+1 — musíme je spojit do jednoho řádku
    call_end_adjusted = call_end  # lokální kopie pro úpravu
    if call_start != call_end and rel + 1 < len(block):
        await_line = block[rel].rstrip()
        if await_line.rstrip().endswith("await"):
            # Sloučíme: "X = await" + "   <inner_l0>"
            next_line = block[rel + 1].lstrip()
            # Strip trailing comma z next_line (pokud tam je)
            if next_line.rstrip().endswith(","):
                next_line = next_line.rstrip()[:-1].rstrip()
            block[rel] = await_line + " " + next_line
            # Smažeme řádek rel+1
            del block[rel + 1]
            call_end_adjusted -= 1  # posun o -1 kvůli smazání

    # 3. Odstraníme matching ")" z call_end
    rel_end = call_end_adjusted - lhs_start
    end_line = block[rel_end]
    last_paren = end_line.rfind(")")
    if last_paren >= 0:
        # Pokud tam byl trailing whitespace, ořežeme
        new_end = end_line[:last_paren].rstrip()
        # Pokud zbyde trailing čárka, odstraníme (z inline arg listu)
        if new_end.endswith(","):
            new_end = new_end[:-1].rstrip()
        block[rel_end] = new_end

    # 4. Re-indentace: všechny řádky bloku o +4 (kromě prázdných)
    # Přeskočíme prázdné řádky, které vznikly odebráním timeout= a )
    new_block = []
    for line in block:
        if not line.strip():
            continue
        new_block.append("    " + line)

    # 5. Vložíme async with na začátek (s indentací rovnou lhs_start)
    lhs_line = lines[lhs_start]
    lhs_indent = len(lhs_line) - len(lhs_line.lstrip())
    new_block.insert(0, " " * lhs_indent + f"async with asyncio.timeout({timeout_str}):")

    return new_block


def migrate_file(filepath: Path) -> tuple[int, list[str]]:
    """Migruje všechny TIGHT wait_for sites v souboru."""
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

        span = find_call_extent(line_only, i)
        if not span:
            i += 1
            continue
        call_start, call_end = span

        if not is_in_try_with_timeout(line_only, call_start):
            warnings.append(f"{filepath.name}:{call_start + 1} — not in try/except TimeoutError")
            i += 1
            continue

        lhs_start, _lhs_end, _eq_pos = find_lhs_span(line_only, call_start)
        timeout_info = find_timeout_arg(line_only, call_start, call_end)
        if not timeout_info:
            warnings.append(f"{filepath.name}:{call_start + 1} — could not extract timeout")
            i += 1
            continue

        timeout_str, t_line, t_col_start, t_col_end = timeout_info

        new_block = transform_block(line_only, lhs_start, call_start, call_end,
                                     timeout_str, t_line, t_col_start, t_col_end)

        # Nahradíme starý blok novým
        line_only[lhs_start:call_end + 1] = new_block
        migrations.append(f"{filepath.name}:{call_start + 1} → timeout({timeout_str})")

        i = lhs_start + len(new_block)

    if migrations:
        filepath.write_text("\n".join(line_only) + "\n", encoding="utf-8")

    return len(migrations), warnings


def main():
    if len(sys.argv) < 2:
        print("Usage: migrate_waitfor_phase2.py <file1.py> [file2.py ...]")
        sys.exit(1)

    total_migrated = 0
    all_warnings = []
    for arg in sys.argv[1:]:
        fp = Path(arg)
        if not fp.exists():
            print(f"  SKIP (not found): {arg}")
            continue
        n_mig, warns = migrate_file(fp)
        total_migrated += n_mig
        all_warnings.extend(warns)
        status = f"  {fp.name}: {n_mig} sites migrated" if n_mig else f"  {fp.name}: 0 changes"
        print(status)
        for w in warns:
            print(f"    WARN: {w}")

    print(f"\n=== TOTAL: {total_migrated} sites migrated across {len(sys.argv) - 1} files")
    if all_warnings:
        print(f"=== WARNINGS: {len(all_warnings)}")
        for w in all_warnings:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
