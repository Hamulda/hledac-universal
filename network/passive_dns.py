"""
Passive DNS — re-export from recon.dns.passive_dns.

K1 (F350M-R): Deduplikace — network/ je infrastructure facade,
kanonická implementace je v recon.dns.passive_dns (obsahuje
retry_backoff_async s RetryableError).
"""
from hledac.universal.recon.dns.passive_dns import (  # noqa: F401, E402
    PassiveDNSResolver,
    PassiveDNSAdapter,
    DOH_RESOLVERS,
    DOH_FALLBACK_CHAIN,
    MAX_DOH_CACHE_SIZE,
    RetryableError,
)

__all__ = [
    "PassiveDNSResolver",
    "PassiveDNSAdapter",
    "DOH_RESOLVERS",
    "DOH_FALLBACK_CHAIN",
    "MAX_DOH_CACHE_SIZE",
    "RetryableError",
]
