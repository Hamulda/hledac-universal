# hledac/universal/export/components/ioc_table_writer.py
# Sprint F11N: Streaming IOC table export
"""
Streaming IOC table section writer.
Yields markdown sections as IOC rows are processed — O(1) memory for large sets.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

__all__ = ["stream_ioc_table_section"]


async def stream_ioc_table_section(
    findings: list[dict],
    *,
    max_rows: int = 500,
    chunk_size: int = 50,
) -> AsyncGenerator[str]:
    """
    Stream IOC table as markdown.

    Yields sections:
      1. Section header
      2. Table header
      3. Chunk rows (chunk_size at a time, yielding between chunks)
      4. Summary footer

    Parameters
    ----------
    findings : list[dict]
        Accepted findings with ioc nodes. Each dict has: finding_id, ioc_nodes, confidence, ts, source_type
    max_rows : int
        Hard cap on IOC rows to prevent unbounded output.
    chunk_size : int
        Rows per yield — allows early flush for large exports.
    """
    if not findings:
        yield "# IOC Table\n\n_No findings with IOCs._\n"
        return

    # ── Section header ──────────────────────────────────────────────
    yield "# IOC Table\n\n"
    yield "| # | IOC Type | Value | Confidence | Source | Observed |\n"
    yield "|---|----------|-------|------------|--------|----------|\n"

    total = 0
    row_num = 0
    chunk_acc = []

    for finding in findings:
        ioc_nodes = finding.get("ioc_nodes") or []
        if not ioc_nodes:
            continue

        for ioc in ioc_nodes:
            if total >= max_rows:
                break

            ioc_type = ioc.get("type", "?") or "?"
            ioc_value = ioc.get("value", "") or ""
            confidence = float(ioc.get("confidence", 0.0))
            source = finding.get("source_type", "?") or "?"
            ts_raw = finding.get("ts")
            if ts_raw:
                if isinstance(ts_raw, (int, float)):
                    import datetime
                    ts_str = datetime.datetime.fromtimestamp(ts_raw, tz=datetime.UTC).strftime("%Y-%m-%d")
                else:
                    ts_str = str(ts_raw)[:10]
            else:
                ts_str = "—"

            row_num += 1
            chunk_acc.append(f"| {row_num} | {ioc_type} | `{ioc_value}` | {confidence:.2f} | {source} | {ts_str} |")

            if len(chunk_acc) >= chunk_size:
                yield "\n".join(chunk_acc) + "\n"
                chunk_acc.clear()
                await asyncio.sleep(0)  # yield to event loop

            total += 1

        if total >= max_rows:
            break

    # Flush remaining
    if chunk_acc:
        yield "\n".join(chunk_acc) + "\n"

    # ── Summary footer ─────────────────────────────────────────────
    yield f"\n_Total: {row_num} IOC rows_"
    if total >= max_rows:
        yield f" (cap: {max_rows})"
    yield "\n"
