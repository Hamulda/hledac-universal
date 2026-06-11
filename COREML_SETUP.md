# CoreML Microservice Setup

## Overview

CoreML microservice runs in a dedicated Python 3.12 venv with full ANE (Apple Neural Engine) support via native `libcoremlpython` and `libmilstoragepython` libraries. The main Hledac process (Python 3.14) communicates with it over HTTP.

## Architecture

```
py3.14 main process py3.12 coremltools venv
┌─────────────────┐          ┌──────────────────────────┐
│ CoreMLClient     │──HTTP──▶│ FastAPI :8765             │
│ (utils/coreml)  │          │ coremltools 9.0 + native │
└─────────────────┘          │ libs (libcoremlpython)    │
                              └──────────────────────────┘
```

---

## Step 1: Install dependencies in py3.12 venv

```bash
uv pip install fastapi uvicorn httpx pydantic psutil --python ~/coremltools/envs/coremltools-py3.12/bin/python
```

Verify:

```bash
~/coremltools/envs/coremltools-py3.12/bin/python -c "import fastapi, uvicorn, httpx, pydantic; print('All deps OK')"
```

Verify:

```bash
~/coremltools/envs/coremltools-py3.12/bin/python -c "import fastapi, uvicorn, httpx, pydantic; print('All deps OK')"
```

---

## Step 2: Add service startup to Hledac

In `runtime/sidecar_orchestrator.py` or your startup sequence:

```python
from hledac.universal.utils.coreml import CoreMLServiceManager

# At startup (once per process)
CoreMLServiceManager.get_instance().start()

# Or via context manager:
async with CoreMLServiceManager() as mgr:
    client = CoreMLClient()
    health = await client.health()
    print(f"CoreML service: {health.status}, ANE: {health.ane}")
```

For auto-start on first use (lazy startup):

```python
from hledac.universal.utils.coreml import CoreMLServiceManager

# Before any CoreML operation
CoreMLServiceManager.ensure_running()
```

---

## Step 3: Verify ANE is working

```python
import asyncio
import numpy as np
from hledac.universal.utils.coreml import CoreMLClient, CoreMLServiceManager

async def benchmark_ane():
    async with CoreMLServiceManager() as mgr:
        client = CoreMLClient()
        health = await client.health()
        print(f"Status: {health.status}")
        print(f"Version: {health.version}")
        print(f"ANE available: {health.ane}")
        print(f"Models in cache: {health.models_loaded}")

        # If you have a model loaded:
        # result = await client.predict("my_model", {"image": np.random.rand(3, 224, 224).tolist()})
        # print(f"Latency: {result.latency_ms:.1f}ms on {result.compute_unit_used}")

asyncio.run(benchmark_ane())
```

Expected output:
```
Status: ok
Version: 9.0
ANE available: True ← if libcoreMLpython loaded OK
Models in cache: 0
```

---

## Step 4: Load a model and run inference

```python
import asyncio
import numpy as np
from hledac.universal.utils.coreml import CoreMLClient, CoreMLServiceManager

async def main():
    async with CoreMLServiceManager() as mgr:
        client = CoreMLClient()

        # Pre-load a model (example with an image classifier)
        # await client.load_model("classifier", "/path/to/model.mlpackage")

        # Run inference
        result = await client.predict(
            model="classifier",
            inputs={"image": np.random.rand(3, 224, 224).tolist()}
        )
        print(f"Latency: {result.latency_ms:.1f}ms on {result.compute_unit_used}")
        print(f"Outputs: {list(result.outputs.keys())}")

asyncio.run(main())
```

---

## Service Log

Logs are written to: `~/Library/Logs/hledac/coreml-service.log`

```bash
tail -f ~/Library/Logs/hledac/coreml-service.log
```

---

## Troubleshooting

### "Connection failed" errors

1. Check if service is running:
   ```bash
   curl http://127.0.0.1:8765/health
   ```

2. Check the log:
   ```bash
   tail ~/Library/Logs/hledac/coreml-service.log
   ```

3. Verify py3.12 venv has fastapi/uvicorn:
   ```bash
   ~/coremltools/envs/coremltools-py3.12/bin/python -c "import fastapi; print(fastapi.__version__)"
   ```

### ANE shows False

This means `libcoremlpython` failed to load. This is normal if:
- macOS SDK is too old (need15+)
- Running in a VM or without Apple Silicon

The service still works with CPU/GPU fallback. ANE acceleration is only used when `compute_unit="all"` and ANE is available.

### Model conversion fails

For `torch` conversion, ensure torch is installed in the py3.12 venv:

```bash
~/coremltools/envs/coremltools-py3.12/bin/pip install torch
```

For `onnx` conversion:

```bash
~/coremltools/envs/coremltools-py3.12/bin/pip install onnx
```

---

## PID File

Service PID is stored in `/tmp/hledac-coreml.pid` for external monitoring.
