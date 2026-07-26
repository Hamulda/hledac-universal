---
title: Discovery Base Adapter Tests
summary: 'Test suite for discovery base: frozen dataclass invariants, RateLimiter token-bucket, abstract mixin enforcement, DuckDuckGo/CRTsh/CirclPDNS/TVNews adapter configs'
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:18:32.783Z'
updatedAt: '2026-07-26T11:18:32.783Z'
---
## Reason
Document discovery adapter tests for BaseDiscoveryMixin, DiscoveryResult, RateLimiter, and concrete adapters

## Raw Concept
**Task:**
Document test_discovery_base.py test suite

**Changes:**
- Added discovery base test coverage
- Tested all 4 discovery adapters

**Files:**
- tests/test_discovery_base.py

**Flow:**
pytest collect -> discover tests -> run async/sync tests -> verify invariants

**Timestamp:** 2026-07-26

## Narrative
### Structure
test_discovery_base.py contains 6 test classes: TestDiscoveryResult, TestRateLimiter, TestBaseDiscoveryMixinAbstractEnforcement, TestDuckDuckGoAdapter, TestCRTshAdapter, TestCirclPDNSAdapter, TestTVNewsAdapter, TestHealthCheck

### Dependencies
Requires pytest-asyncio, pytest.mark.asyncio decorator for async tests

### Highlights
DiscoveryResult is frozen dataclass with slots. RateLimiter implements token-bucket algorithm. BaseDiscoveryMixin enforces abstract requirements at instantiation. Each adapter has distinct rate limits: DuckDuckGo 60rpm/35s, CRTsh 30rpm/8s, CirclPDNS 30rpm/8s, TVNews 20rpm/15s.

### Rules
Rule 1: DiscoveryResult fields query, url, title, snippet, source, source_type are frozen after construction
Rule 2: RateLimiter.available starts equal to burst_size and refills at _refill_rate tokens per second
Rule 3: BaseDiscoveryMixin concrete subclasses MUST implement name, source_type, and _do_discover
Rule 4: DuckDuckGoAdapter timeout is 35s (higher than base 8s) due to search API latency

### Examples
TestDiscoveryResult: pytest.raises(AttributeError) when assigning to frozen field
TestRateLimiter: burst_size ceiling enforces max concurrent requests
TestBaseDiscoveryMixinAbstractEnforcement: TypeError raised if any required attr missing
