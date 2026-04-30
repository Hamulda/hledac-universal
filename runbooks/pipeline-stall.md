# Runbook: Pipeline Stall

## Symptoms
- No new findings for extended period
- SprintScheduler appears frozen
- `in_progress` count stuck
- No log output for > 5 minutes

## Diagnosis

### 1. Check scheduler state
```python
from runtime.sprint_scheduler import SprintScheduler
scheduler = SprintScheduler()
state = scheduler.get_state()
print(f"Active: {state.active_requests}")
print(f"Pending: {state.pending_count}")
print(f"Blocked: {state.blocked_domains}")
```

### 2. Check for deadlocks
```bash
# Find stuck Python processes
ps aux | grep python | grep -v grep

# Check thread stack traces
kill -3 <pid>  # Send SIGQUIT, prints stack trace
```

### 3. Check event loop
```python
import asyncio
loop = asyncio.get_event_loop()
print(f"Running tasks: {len(asyncio.all_tasks(loop))}")
print(f"Closed: {loop.is_closed()}")
```

### 4. Check domain blocklist
```python
from coordinators.fetch_coordinator import FetchCoordinator
fc = FetchCoordinator()
blocked = fc.get_blocked_domains()
print(f"Blocked domains: {len(blocked)}")
```

## Common Causes

### Circuit Breaker Triggered
- Too many failures to a domain
- Check: `FetchCoordinator.get_blocked_domains()`
- Solution: Wait for cooldown or reset manually

### Resource Exhaustion
- No RAM available for new tasks
- Check: `resource_governor.sample_uma_status()`
- Solution: Cancel low-priority tasks, clear cache

### Async Task Leak
- Tasks created but never completed
- Check: `len(asyncio.all_tasks())` > expected
- Solution: Cancel stale tasks

### MLX Model Swap
- Model reload causes stall
- Normal: 10-30 seconds for model swap
- Abnormal: Check for OOM during swap

### Network Partition
- All fetches timing out
- Check: `transport_counters` for error patterns
- Solution: Check firewall, DNS, proxy settings

## Recovery

### Quick Resume
```python
# Reset stalled scheduler state
scheduler = SprintScheduler()
scheduler._reset_result()  # Clear in-progress state

# Resume from checkpoint
from core.checkpoint import CheckpointStore
store = CheckpointStore('data/checkpoints')
store.restore_latest()
```

### Force Pipeline Unblock
```python
# Reset circuit breakers
from coordinators.fetch_coordinator import FetchCoordinator
fc = FetchCoordinator()
fc._domain_failures.clear()
fc._domain_cooldowns.clear()

# Clear MLX cache
import mlx.core as mx
mx.eval([])
mx.metal.clear_cache()

# Force garbage collection
import gc
gc.collect()
```

### Full Restart
```bash
# 1. Save current state
python3 -c "
from core.checkpoint import CheckpointStore
store = CheckpointStore('data/checkpoints')
store.create('pre-restart')
"

# 2. Stop service gracefully
kill -TERM <pid>

# 3. Verify no zombie processes
pkill -f hledac

# 4. Clear temporary state
rm -rf data/tmp/*
rm -rf cache/*

# 5. Restart
python3 -m hledac.universal
```

## Prevention
- Set `branch_timeout_count` alerts
- Monitor `findings_per_hour` rate
- Use health checks: `/health` endpoint
- Implement watchdog task for long-running sprints
- Log all state transitions for post-mortem
