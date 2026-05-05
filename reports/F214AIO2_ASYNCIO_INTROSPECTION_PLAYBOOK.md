# F214AIO-2: Asyncio Introspection Playbook

## Purpose

Safe operator helper for diagnosing stuck Python asyncio sprints using Python 3.14's built-in `asyncio` module introspection commands (`ps`, `pstree`).

**This tool is MANUAL ONLY. No runtime integration, no automatic monitoring, no hot-path impact.**

---

## Quick Start

### 1. Start a sprint in background

```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac
PYTHONPATH="$PWD" python -m hledac.universal.__main__ &
SPRINT_PID=$!
echo "Sprint PID: $SPRINT_PID"
```

### 2. If sprint gets stuck

```bash
# Dump asyncio task state
PYTHONPATH="$PWD" python hledac/universal/tools/dump_asyncio_tasks.py $SPRINT_PID
```

### 3. Inspect outputs

```bash
# Outputs go to reports/runtime_dumps/
ls -la reports/runtime_dumps/asyncio_*_${SPRINT_PID}_*

# View ps output
cat reports/runtime_dumps/asyncio_ps_${SPRINT_PID}_*.txt

# View pstree output
cat reports/runtime_dumps/asyncio_pstree_${SPRINT_PID}_*.txt
```

---

## Tool Reference

### `tools/dump_asyncio_tasks.py`

**Purpose:** Manually dump asyncio task state for a running process.

**Arguments:**
| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `pid` | int | Yes | Process ID to inspect |
| `--output-dir`, `-o` | str | No | Output directory (default: `reports/runtime_dumps/`) |
| `--timeout`, `-t` | float | No | Timeout per command (default: 10.0s) |

**Output files:**
- `reports/runtime_dumps/asyncio_ps_<pid>_<timestamp>.txt` — Task list
- `reports/runtime_dumps/asyncio_pstree_<pid>_<timestamp>.txt` — Task tree

**Exit codes:**
| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Process not found or error during dump |

---

## Python 3.14 `asyncio ps` / `pstree`

Python 3.14 adds built-in asyncio task introspection:

```bash
# List all asyncio tasks
python -m asyncio ps <pid>

# Show task parent-child relationships
python -m asyncio pstree <pid>
```

These commands are only available in Python 3.14+.

---

## Example Workflow

```
# Terminal 1: Start sprint
$ cd /Users/vojtechhamada/PycharmProjects/Hledac
$ PYTHONPATH="$PWD" python -m hledac.universal.__main__ &
[1] 67890
Sprint started with PID 67890

# Sprint appears stuck after some time...

# Terminal 2: Inspect
$ PYTHONPATH="$PWD" python hledac/universal/tools/dump_asyncio_tasks.py 67890
Saving: reports/runtime_dumps/asyncio_ps_67890_1746489600.txt
Saving: reports/runtime_dumps/asyncio_pstree_67890_1746489600.txt

Dumped 2 file(s):
  /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/reports/runtime_dumps/asyncio_ps_67890_1746489600.txt
  /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/reports/runtime_dumps/asyncio_pstree_67890_1746489600.txt
Done.

# View the dumps
$ cat reports/runtime_dumps/asyncio_ps_67890_1746489600.txt
=== asyncio ps for PID 67890 ===
Timestamp: 2026-05-05 12:00:00 (epoch 1746489600)
Command: python -m asyncio ps 67890
Return code: 0

--- STDOUT ---
    ID          Name                       State
------------------------------------------------
<Task 1>     Task-1                    RUNNING
<Task 2>     Task-2                    WAITING
<Task 3>     SprintScheduler.run       SUSPENDED

# Task tree
$ cat reports/runtime_dumps/asyncio_pstree_67890_1746489600.txt
=== asyncio pstree for PID 67890 ===
...

```

---

## Design Constraints (Invariant Enforcement)

| Constraint | Enforcement |
|------------|-------------|
| No runtime behavior change | Helper only, no imports from main codebase |
| No new dependencies | Uses only stdlib: `subprocess`, `argparse`, `os`, `pathlib`, `time` |
| No background monitor | No auto-spawn, no signal handlers, no atexit hooks |
| No hot-path impact | Zero imports from `hledac.universal.*` |
| Manual only | Script requires explicit PID argument to run |

---

## Validation Commands

```bash
# Syntax check
python -m py_compile tools/dump_asyncio_tasks.py

# Help output
PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac python tools/dump_asyncio_tasks.py --help
```

---

## Troubleshooting

### "python: -m asyncio: command not found"

Python 3.14+ required. Check version:

```bash
python --version  # Must be 3.14+
```

### "Error: Process N does not exist"

Process exited or PID is wrong. Verify with:

```bash
ps aux | grep python
```

### "Timeout after 10.0s"

Increase timeout with `-t 30` flag, or process is genuinely unresponsive.

### Empty output files

Python 3.14 `asyncio ps/pstree` require the process to have an active asyncio event loop. If the process uses `asyncio.run()` or `loop.run_until_complete()` in a way that doesn't expose the loop, output may be empty.

---

## Files

| File | Purpose |
|------|---------|
| `tools/dump_asyncio_tasks.py` | Main helper script |
| `reports/runtime_dumps/` | Output directory for dumps (created automatically) |
| `reports/F214AIO2_ASYNCIO_INTROSPECTION_PLAYBOOK.md` | This document |
