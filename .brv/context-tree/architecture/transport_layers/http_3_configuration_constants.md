---
title: HTTP/3 Configuration Constants
summary: 'M1_BOUNDS constants for HTTP/3: 512 cache max, 3 concurrency, 8s timeout, 5.5GiB RSS block, 16 probe tasks'
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:19:10.467Z'
updatedAt: '2026-07-26T11:19:10.467Z'
---
## Reason
Documenting M1_BOUNDS configuration for HTTP/3

## Raw Concept
**Task:**
Document HTTP/3 configuration constants from M1_BOUNDS

**Timestamp:** 2026-07-26

## Narrative
### Structure
Constants sourced from M1_BOUNDS() for HTTP/3 lane configuration

## Facts
- **http3_cache_max**: HTTP/3 LRU cache max is 512 entries (M1_BOUNDS.http3_lru_max) [project]
- **http3_concurrency_max**: HTTP/3 concurrency max is 3 (M1_BOUNDS.http3_concurrency_max) [project]
- **http3_timeout_s**: HTTP/3 timeout is 8.0 seconds [project]
- **http3_wait_timeout_s**: HTTP/3 semaphore wait timeout is 2.0 seconds [project]
- **http3_cache_ttl_s**: HTTP/3 cache TTL is 86400 seconds (M1_BOUNDS.http_cache_ttl_s) [project]
- **http3_rss_block_gib**: HTTP/3 RSS memory block threshold is 5.5 GiB (M1_BOUNDS.fetch_soft_ceiling_gb) [project]
- **max_probe_tasks**: Max probe tasks for speculative Alt-Svc is 16 [project]
- **head_probe_timeout_s**: HEAD probe timeout is 4.0 seconds [project]
