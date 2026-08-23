# transport-tor

## Kind

`module`

## Status

`Preferred`

## Last Verified

- Date: 2026-08-20
- Evidence:
  - `transport/tor_transport.py`: Source verification complete

## Evidence Level

`Source-Verified`

## Tags

- anonymity
- tor
- dark-web
- transport

## Summary

Tor anonymity network transport via Stem controller or Arti client. Provides circuit rotation, exit node control, and stealth connectivity for dark web reconnaissance.

## Entry Points

- `transport.tor_transport.TorTransport`: Main class
- `new_circuit()`: Rotate to new circuit
- `fetch(url)`: Fetch via Tor

## Key Files

- `transport/tor_transport.py`: Main implementation
- `transport/arti_transport.py`: Rust Arti fallback

## Related Entries

- `modules/transport-arti.md`: Rust Arti implementation
- `modules/transport-i2p.md`: I2P transport
- `modules/recon-dark-web-lane.md`: Dark web recon using Tor

## Owns Responsibility

Anonymized HTTP fetching via Tor network

## Inputs

- URL to fetch
- Optional: exit node country code

## Outputs

- HTTP response (same interface as curl_cffi)

## Side Effects

- Tor daemon circuit state modified

## Use When

- Dark web reconnaissance
- Circumventing censorship
- Anonymity required

## Do Not Use When

- Performance critical (Tor adds ~500ms latency)
- Legal risk concerns (exit node location)
- Clearnet where stealth not needed

## Known Constraints

- Circuit rotation: every 10 requests or 5 minutes
- Requires Tor daemon running separately

## Notes For Agents

- Circuit rotation configurable via `TOR_EXIT_COUNTRY`
- Fallback to regular transport if Tor unavailable
- Tor daemon ~50MB RSS
