"""
Re-export from recon.bgp_advisor_adapter — canonical BGP implementation.

K2 (F350M-R): network/ is infrastructure facade.
Canonical BGP adapter is recon.bgp_advisor_adapter.
"""

from hledac.universal.recon.bgp_advisor_adapter import (  # noqa: F401, E402
    BGPAdvisorAdapter,
    create_bgp_advisor_adapter,
)

__all__ = [
    "BGPAdvisorAdapter",
    "create_bgp_advisor_adapter",
]
