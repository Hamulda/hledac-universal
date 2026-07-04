#!/usr/bin/env python3
"""Check current dependency versions against targets."""
import json, subprocess, sys

result = subprocess.run(['uv', 'pip', 'list', '--format=json'], capture_output=True, text=True)
data = json.loads(result.stdout)

key = ['duckdb','httpx','aiohttp','pydantic','orjson','lmdb','prometheus','mlx-lm','mlx','uvloop','hishel','curl-cffi','lancedb','msgspec','pyarrow','polars','structlog','jinja2','psutil']

for r in data:
    n = r.get('name','')
    for k in key:
        if k.lower() in n.lower():
            print(f"{n}=={r.get('version','?')}")
            break
