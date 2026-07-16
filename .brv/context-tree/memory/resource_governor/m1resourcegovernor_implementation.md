---
consolidated_at: '2026-07-16T11:30:24.784Z'
consolidated_from: [{date: '2026-07-16T11:30:24.784Z', path: memory/resource_governor/uma_memory_management.md, reason: 'Both files document ResourceGovernor UMA memory management. m1resourcegovernor_implementation.md (2026-07-16) is the canonical successor with more detailed state machine (5 states), specific thresholds (5632 MB, swap tiers, thermal 82°C), lock ordering rules, and TTL policies. uma_memory_management.md (2026-07-11) has older ''soft_warn'' state terminology and lacks current implementation details. Merge into m1resourcegovernor_implementation.md to preserve all unique facts (dual-channel TTL cache mention, concurrency presets with specific values, M1/M2/M3 calibration).'}]
related: [memory/resource_governor/m1_8gb_ram_priority_optimizations.md]
---
## Additional Facts from uma_memory_management.md
- **dual_channel_ttl_cache**: Dual-channel TTL cache implemented alongside swap tiered policy [project]
- **hardware_calibration**: Supports M1 8GB (6.25GB), M2 16GB, M3 24GB configurations [project]
- **concurrency_presets**: emergency=0 workers, critical=1, warn=3, soft_warn=5, ok=5 with varying fetch limits [project]
- **flow**: memory_check -> hysteresis_state -> concurrency_adjust -> action_take [project]
- **dependencies**: psutil for memory detection, concurrent.futures for worker pool management [project]

## Historical Note
The original uma_memory_management.md (2026-07-11) documented ResourceGovernor with 'soft_warn' terminology. This has been superseded by m1resourcegovernor_implementation.md (2026-07-16) which uses the canonical 5-state machine (NORMAL→ELEVATED→CRITICAL→EMERGENCY→CIRED) with dual-channel TTL hysteresis and updated ConcurrencyPreset calibration.