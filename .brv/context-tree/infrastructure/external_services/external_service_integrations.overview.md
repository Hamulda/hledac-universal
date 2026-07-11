**Key Points:**
- Tor SOCKS proxy integration at 127.0.0.1:9050 with automatic circuit renewal every 10 requests and 2.0x timeout scaling
- I2P SOCKS proxy integration at 127.0.0.1:7654 for anonymous routing
- IPFS gateway access via IPFSDSidecarAdapter for distributed content retrieval
- Threat intelligence API integration (Shodan, Censys, GreyNoise) controlled via feature flags
- BGP enrichment provided through a dedicated sidecar service
- All proxy routing consolidated in transport/http3_lane.py

**Structure:**
- Reason (document purpose)
- Raw Concept (task definition, changes, files, flow)
- Narrative (structure overview, dependencies, highlights)
- Facts (key-value pairs with source tags)

**Notable Entities:**
- Tor SOCKS (9050), I2P SOCKS (7654)
- IPFSDSidecarAdapter
- Shodan, Censys, GreyNoise APIs
- BGP sidecar
- http3_lane.py