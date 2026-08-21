"""CT log pivot — runtime infrastructure module.

Provides run_ct_pivot() for CT (Certificate Transparency) log discovery.
Can be imported from anywhere in the runtime without creating circular

dependencies.

F350M-R: Extracted from sprint_entrypoint.py to break the reverse-import
dependency: scheduler_v2/acquisition.py no longer imports from the CLI
entry-point layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CTPivotResult:
    """Return type for run_ct_pivot."""

    domain: str
    accepted_findings: int
    cert_count: int
    first_cert: str
    last_cert: str
    san_names: list[str]
    issuers: list[str]


async def run_ct_pivot(domain: str) -> CTPivotResult:
    """
    Run CT log pivot for a single domain.

    F350M-R: Return type changed from None → CTPivotResult.
    Previous void return was a bug: _run_ct_branch in acquisition.py
    called getattr(_result, "accepted_findings", 0) which always
    returned 0 because the old run_ct_pivot returned None.

    Args:
        domain: Domain to pivot CT logs for.

    Returns:
        CTPivotResult with accepted_findings (SAN count from CT logs)
        and metadata about the pivot.
    """
    from hledac.universal.intel.ct_log_client import CTLogClient
    from hledac.universal.paths import TOR_ROOT
    from hledac.universal.transport.session_pool import session_pool
    from hledac.universal.transport.tor_transport import TorTransport

    ct_client = CTLogClient(TOR_ROOT.parent / "cache" / "crt")
    tor_transport = TorTransport()
    tor_started = await tor_transport.start()
    if tor_started:
        logger.info("Tor ready for .onion fetches")
    else:
        logger.warning("Tor unavailable — .onion sources disabled")
    try:
        sess = await session_pool.httpx()
        result: dict[str, Any] = await ct_client.pivot_domain(domain, sess)
        _san_names: list[str] = result.get("san_names", [])
        _accepted = len(_san_names)
        print(f"\nCT LOG PIVOT: {result['domain']}")
        print(f"  Cert count:  {result['cert_count']}")
        print(f"  First cert: {result['first_cert']}")
        print(f"  Last cert:  {result['last_cert']}")
        print(f"  SAN domains: {len(_san_names)}")
        for san in _san_names[:10]:
            print(f"    {san}")
        if _san_names and len(_san_names) > 10:
            print(f"    ... (+{len(_san_names) - 10} more)")
        print(f"  Issuers: {result['issuers']}")
        return CTPivotResult(
            domain=result["domain"],
            accepted_findings=_accepted,
            cert_count=result["cert_count"],
            first_cert=result["first_cert"],
            last_cert=result["last_cert"],
            san_names=_san_names,
            issuers=result["issuers"],
        )
    finally:
        await tor_transport.stop()
        logger.info("CT pivot done, Tor stopped")
