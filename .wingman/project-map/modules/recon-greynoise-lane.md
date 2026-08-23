# recon-greynoise-lane

**Type:** Recon Lane  
**Path:** `recon/greynoise_lane.py`  
**Status:** current

## Purpose

GreyNoise threat intelligence lane. Classifies IP traffic as malicious, benign, or unknown.

## Key Functions

| Function | Purpose |
|----------|---------|
| `GreynoiseLane` | AcquisitionLane class |
| `enrich(ip)` | Enrich IP with threat context |
| `bulk_enrich(ips)` | Batch IP enrichment |

## Classification

| Classification | Meaning |
|----------------|---------|
| malicious | Known malicious actor |
| benign | Whitelisted traffic |
| unknown | First seen, unclassified |
| ibm | IBM X-Force feed |

## Invariants

- [RGL-1] Rate limit: 1000 req/day (free tier)
- [RGL-2] Cache: 24 hour TTL for results
- [RGL-3] API key: `GREYNOISE_API_KEY`
