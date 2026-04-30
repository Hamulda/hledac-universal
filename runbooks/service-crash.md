# Runbook: Service Crash

## Symptoms
- Process exits unexpectedly
- No response from API/CLI
- Error logs show unhandled exception

## Diagnosis

### 1. Check logs
```bash
tail -100 logs/hledac.log
journalctl -u hledac --no-pager -n 100
```

### 2. Check exit code
```bash
echo $?  # Previous command exit code
```

### 3. Check for M1 memory issues
```bash
ps aux | grep python
top -l 1 | grep Python
```

## Common Causes

### OOM Kill (M1 8GB constraint)
- System kills process when RAM > 6.5GB
- Solution: Reduce `MAX_RAM_GB` in resource_allocator.py

### asyncio.run() in thread (M1 crash vector)
- Nested event loops crash M1
- Solution: Use `loop.run_until_complete()` instead

### Signal termination
- Check `dmesg | grep -i kill`
- May be system OOM killer

## Recovery
1. Restart service with reduced concurrency
2. Check MLX cache state: `mx.eval([]); mx.metal.clear_cache()`
3. Verify duckdb integrity
4. Resume from last checkpoint

## Prevention
- Monitor RAM with `resource_governor.sample_uma_status()`
- Set alerts at 85% memory pressure
- Run with `--hermetic` flag for benchmarks
