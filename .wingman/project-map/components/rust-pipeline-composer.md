# Rust Pipeline Composer

## Metadata

- **Entry Path:** components/rust-pipeline-composer
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** component

## Summary

High-performance string pipeline via Rust backend.

## Source Paths

- rust_extensions/wiring/pipeline_compose_wiring.py

## Operations

| Operation | Functions |
|-----------|-----------|
| map | len, lower, upper, strip, hash_xxh3 |
| filter | not_empty, has_at, has_scheme, is_ascii, len_lt_2048 |
| filter_map | Combined filter + map |

## Usage

```python
from rust_extensions.wiring.pipeline_compose_wiring import RustPipelineComposer

pipeline = (
    RustPipelineComposer()
    .add_filter("not_empty")
    .add_map("lower")
    .add_map("strip")
)
results = await pipeline.run(items)
```

## Batch Processing

Bounded batch size for M1 memory safety.
