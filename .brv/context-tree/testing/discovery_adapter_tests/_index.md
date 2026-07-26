---
children_hash: 425aedae2d43774632e43449872fff81bfdbf37c51531fbf3706d360ee37e9b0
compression_ratio: 0.5155038759689923
condensation_order: 1
covers: [discovery_base_adapter_tests.md]
covers_token_total: 516
summary_level: d1
token_count: 266
type: summary
---
<think>
I need to create a concise structural summary of the discovery_base_adapter_tests.md knowledge entry, compressing it to ~20-30% of the input while preserving key facts.
</think>

## Discovery Base Adapter Tests

Test suite covering `tests/test_discovery_base.py` with 6 test classes validating discovery adapters.

**Core Components:**
- **DiscoveryResult** — Frozen dataclass with slots; fields: query, url, title, snippet, source, source_type
- **RateLimiter** — Token-bucket implementation; burst_size ceiling, refill_rate tokens/second
- **BaseDiscoveryMixin** — Enforces abstract requirements (name, source_type, _do_discover) at instantiation; raises TypeError if missing

**Adapter Rate Limits:**

| Adapter | RPM | Timeout |
|---------|-----|---------|
| DuckDuckGo | 60 | 35s |
| CRTsh | 30 | 8s |
| CirclPDNS | 30 | 8s |
| TVNews | 20 | 15s |

**Key Invariants:**
- DiscoveryResult fields immutable after construction (pytest.raises(AttributeError) on assignment)
- BaseDiscoveryMixin subclasses must implement name, source_type, and _do_discover