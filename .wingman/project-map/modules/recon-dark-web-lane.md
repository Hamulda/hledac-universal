# recon-dark-web-lane

**Type:** Recon Lane  
**Path:** `recon/dark_web_lane.py`  
**Status:** current

## Purpose

Dark web reconnaissance via Tor/I2P. Indexes onion/eepsite destinations.

## Key Functions

| Function | Purpose |
|----------|---------|
| `DarkWebLane` | AcquisitionLane class |
| `crawl_onion(url)` | Crawl .onion site |
| `search_onion(query)` | Search dark web |
| `index_destination(url)` | Index dark web content |

## Invariants

- [RDW-1] Requires Tor transport
- [RDW-2] Crawl delay: 5-10s between requests
- [RDW-3] Respect robots.txt on dark web
- [RDW-4] JA3 spoofing enabled

## M1 Memory Notes

Session isolation per destination. ~100MB per concurrent crawl.
