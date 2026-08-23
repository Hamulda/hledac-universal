# network-bgp-monitor

**Type:** Network Intelligence  
**Path:** `network/bgp_monitor.py`  
**Status:** current

## Purpose

BGP monitoring and ASN intelligence for network attribution.

## Key Functions

| Function | Purpose |
|----------|---------|
| `BGPMonitor` | Main class |
| `get_prefix(ip)` | Get BGP prefix |
| `get_asn_info(asn)` | Get ASN details |
| `trace_route(ip)` | Traceroute with ASN |

## Data Sources

| Source | Protocol |
|--------|----------|
| RouteViews | BGP dumps |
| RIPE RIS | Live BGP |
| RPKI | ROA validation |

## Invariants

- [NBM-1] ASN lookup: ~50ms
- [NBM-2] Prefix lookup: cached 24h
- [NBM-3] RouteViews update: daily

## Dependencies

- `pyasn` for local BGP lookups
- `httpx` for RIPE API
