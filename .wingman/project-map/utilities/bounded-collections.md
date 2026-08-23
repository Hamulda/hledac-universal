# Bounded Collections

## Metadata

- **Entry Path:** utilities/bounded-collections
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** utility

## Summary

Memory-safe collections with explicit size limits for M1 8GB.

## Bounds

| Collection | Max Size |
|------------|----------|
| MAX_CLAIMS | 5000 |
| MAX_HOST_PENALTIES | 512 |
| MAX_BLOOM_TIERS | 256 |
| MAX_CACHED_PROMPTS | 8 |
| MAX_HYPOTHESIS_NODES | 5000 |
| MAX_HYPOTHESIS_EDGES | 20000 |

## Pattern

```python
class BoundedList:
    def __init__(self, max_size: int):
        self._items: list = []
        self._max_size = max_size

    def append(self, item):
        if len(self._items) >= self._max_size:
            self._items.pop(0)  # LRU
        self._items.append(item)
```

## Anti-Pattern

ScalableBloomFilter - grows without limit.
