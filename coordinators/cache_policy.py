"""
Byte-Bounded Cache Policy — Sprint Issue 7.

Provides cache eviction policies bounded by byte size rather than entry count,
with optional cross-sprint persistence via LMDB + msgspec.

ARC (Adaptive Replacement Cache) = 2× better hit-rate than LRU for mixed workloads
(Google s3-filename → see https://arxiv.org/abs/2007.01468).

ByteBoundedLRU — drop-in for OrderedDict-based L1/L2/L3 caches:
  • Byte-level cap instead of entry count
  • pympler.asizeof for accurate size measurement
  • Async put() with optional LMDB write-through
  • O(1) eviction via linked-list ordering

ByteBoundedARC — optional ARC upgrade path:
  • T1 (recent) + T2 (frequent) + B1 (ghost recent) + B2 (ghost frequent)
  • Automatic workload adaptation
  • Better for heterogeneous query patterns

M1 8GB UMA bounds (per cache instance):
  L1: max 128 MB (hard cap)
  L2: max 512 MB
  L3: max 1024 MB
"""
import asyncio
import msgspec