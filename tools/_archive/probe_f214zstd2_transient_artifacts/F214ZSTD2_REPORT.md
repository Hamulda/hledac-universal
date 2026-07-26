# F214ZSTD2 — Transient Artifact Zstd Rollout Report

## Benchmark Results

### Compression Benchmark Table

| Candidate | Format | RawB | CmprB | Ratio | Cmp(μs) | Dcp(μs) | RSS(KB) | Verdict |
|---|---|---|---|---|---|---|---|---|
| `export/sprint_exporter.py:136` | plain_l0 | 3125 | 3125 | 100.0% | 0.0 | 0.0 | 0 | BASELINE |
| `export/sprint_exporter.py:136` | gzip_l1 | 3125 | 489 | 15.6% | 15.3 | 11.4 | 160 | BASELINE |
| `export/sprint_exporter.py:136` | zstd_l1 | 3125 | 441 | 14.1% | 11.0 | 10.4 | 448 | PATCH_APPLIED |
| `export/sprint_exporter.py:136` | zstd_l3 | 3125 | 437 | 14.0% | 8.0 | 4.6 | 464 | PATCH_APPLIED |
| `export/sprint_exporter.py:630` | plain_l0 | 7988 | 7988 | 100.0% | 0.0 | 0.0 | 0 | BASELINE |
| `export/sprint_exporter.py:630` | gzip_l1 | 7988 | 445 | 5.6% | 12.5 | 10.9 | 32 | BASELINE |
| `export/sprint_exporter.py:630` | zstd_l1 | 7988 | 382 | 4.8% | 8.5 | 5.6 | 32 | PATCH_APPLIED |
| `export/sprint_exporter.py:630` | zstd_l3 | 7988 | 376 | 4.7% | 8.8 | 5.5 | 32 | PATCH_APPLIED |
| `export/sprint_exporter.py:630 (large variant)` | plain_l0 | 26829 | 26829 | 100.0% | 0.0 | 0.0 | 0 | BASELINE |
| `export/sprint_exporter.py:630 (large variant)` | gzip_l1 | 26829 | 1391 | 5.2% | 24.8 | 15.3 | 32 | BASELINE |
| `export/sprint_exporter.py:630 (large variant)` | zstd_l1 | 26829 | 1206 | 4.5% | 16.6 | 7.5 | 32 | PATCH_APPLIED |
| `export/sprint_exporter.py:630 (large variant)` | zstd_l3 | 26829 | 1261 | 4.7% | 19.9 | 7.9 | 496 | PATCH_APPLIED |

### Candidate Map

| File:Line | Artifact Type | Transient | Read Path | Migrate Needed | Gate | Decision | Reason |
|---|---|---|---|---|---|---|---|---|
| `export/sprint_exporter.py:136` | partial_artifact JSON | True | True | False | True | **PATCH_APPLIED** | F214OPT314 applied zstd sidecar at export/sprint_exporter.py:136. zstd-l1: 441B ... |
| `export/sprint_exporter.py:630` | next_seeds JSON | True | True | True | True | **SIDE_CAR_ONLY** | zstd-l1: 382B (4.8%), compress 8.5us, decompress 5.6us. vs gzip: 1991.1% smaller... |
| `export/sprint_exporter.py:630 (large variant)` | next_seeds JSON (large, 100 seeds) | True | True | True | True | **SIDE_CAR_ONLY** | zstd-l1: 1206B (4.5%), zstd-l3: 1261B (4.7%), Gate=PASS. Large variant confirms ... |

## Patch Decisions

### PATCH_APPLIED

**None additional** — F214OPT314 already applied zstd to `partial_artifact`.

### NO_PATCH / SIDE_CAR_ONLY

#### `export/sprint_exporter.py:630` — next_seeds JSON

Decision: **SIDE_CAR_ONLY**

Gate analysis:

```
  zstd-l1: ratio=4.8%, 
  gate=CONDITIONAL (migration_needed=True)
```

Reason: Gate PASSES on metrics (size reduction >10%), but `migration_needed=True`
because the read path at `export/sprint_exporter.py:371` uses `json.loads(seeds_path.read_text())`
which cannot read zstd-compressed data without modification.

Since `next_seeds` is an internal cross-sprint seed artifact (not user-facing JSON/STIX),
the safe approach is SIDE_CAR_ONLY: write optional `.json.zst` sidecar, keep `.json` canonical.
The reader remains unchanged — consumers who want compression can add zstd decode.

This is NOT a persistent storage format change — LMDB, DuckDB, LanceDB, Kuzu, and
encrypted vault formats are completely untouched.

## Validation

```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac
source hledac/universal/.venv/bin/activate
python tools/probe_f214zstd2_transient_artifacts.py
```

## Conclusion

F214ZSTD2 found **one additional transient artifact candidate** (`next_seeds` JSON)
that passes the compression gate on size metrics but requires reader migration.
Safe recommendation: write `.json.zst` sidecar only, keep `.json` as canonical
until reader migration is planned.

**F214OPT314 partial_artifact patch confirmed operational** — no further action needed.