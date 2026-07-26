---
children_hash: f9dd7311cf38f1771719f5e8752396e06606eeab59fec60fc8e5c38bac76f2b7
compression_ratio: 0.9180327868852459
condensation_order: 1
covers: [discovery_adapter_tests.md]
covers_token_total: 366
summary_level: d1
token_count: 336
type: summary
---
<think>
The user wants me to create a structural summary from a single knowledge entry about discovery adapter tests. I need to distill this down to the essential facts while preserving key technical details.
</think>

## Discovery Adapter Tests

Tests cover the discovery adapter framework in `tests/test_discovery_base.py` including abstract enforcement, data structures, rate limiting, and 4 concrete implementations.

### Test Components

- **TestDiscoveryResult** — Validates frozen dataclass immutability and default field values
- **TestRateLimiter** — Tests token bucket algorithm via async `acquire()` method
- **TestBaseDiscoveryMixinAbstractEnforcement** — Ensures abstract class cannot be instantiated directly

### Concrete Adapters Tested

| Adapter | Rate Limit |
|---------|------------|
| DuckDuckGo | 60 requests/min |
| CRTsh | 30 requests/min |
| CirclPDNS | 30 requests/min |
| TVNews | 20 requests/min |

### Key Design Details

- `DiscoveryResult` uses `@frozen` dataclass with `__slots__` for immutability
- `RateLimiter` implements token bucket pattern for async throttling
- Each adapter extends `BaseDiscoveryMixin` abstract base
- Health check integration tested across all concrete adapters

### Test Dependencies

Requires `pytest.mark.asyncio` for async test execution and `pytest.raises` for exception validation.