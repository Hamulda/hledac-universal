"""Public discovery — URL generation, provider selection, and _DiscoveryEngine.

Extracted from live_public_pipeline.py.
Handles: rescue URLs, bootstrap, keyword search, CT/CC/Onion injection,




         _DiscoveryEngine (discovery loop, academic search, TOT).

DI seam: set `_async_discovery_search_var`, `_ct_scanner_var`,
and `_async_search_multi_engine_var` via `_patch_*()` helpers to override defaults.
"""
import asyncio
import contextvars
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    pass
logger = logging.getLogger(__name__)
from .public_constants import _is_threat_query as _is_threat_query_impl
_async_discovery_search_var: contextvars.ContextVar[Any] = contextvars.ContextVar("_async_discovery_search_var", default=None)
_async_search_multi_engine_var: contextvars.ContextVar[Any] = contextvars.ContextVar("_async_search_multi_engine_var", default=None)

def _patch_discovery(search_fn: Any) -> None:
    """DI: override the async discovery search function."""
    _async_discovery_search_var.set(search_fn)

def _ensure_discovery_patched() -> None:
    """Ensure discovery search is patched; fall back to duckduckgo if not."""
    if _async_discovery_search_var.get() is None:
        from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web
        _async_discovery_search_var.set(async_search_public_web)
    if _async_search_multi_engine_var.get() is None:
        from hledac.universal.discovery.duckduckgo_adapter import search_multi_engine as _search_multi_engine_bootstrap
        _async_search_multi_engine_var.set(_search_multi_engine_bootstrap)

class FetchPolicy(msgspec.Struct, frozen=True, gc=False):
    """Bounded fetch policy for canonical public sprint."""

    use_js: bool = False
    use_doh: bool = False
    use_stealth: bool = False

    @classmethod
    def default(cls) -> FetchPolicy:
        """Return default fetch policy (no JS, no DOH, no stealth)."""
        return cls(use_js=False, use_doh=False, use_stealth=False)

    @classmethod
    def js_capable(cls) -> FetchPolicy:
        """Return fetch policy with JavaScript rendering enabled."""
        return cls(use_js=True, use_doh=False, use_stealth=False)

    @classmethod
    def tor_like(cls) -> FetchPolicy:
        """Return stealth-like fetch policy (no JS, no DOH, stealth)."""
        return cls(use_js=False, use_doh=False, use_stealth=True)

def _compute_fetch_policy(url: str, discovery_score: float | None, discovery_reason: str | None, strong_signal: bool) -> FetchPolicy:
    """Determine fetch policy per URL based on discovery metadata.

    DI seam: Reads _ASYNC_FETCH_PUBLIC_TEXT, _SYNC_MATCH_TEXT via
    _ensure_patched() from the fetch module.
    """
    _JS_DOMAINS = ("google.com", "youtube.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "reddit.com", "linkedin.com", "github.com", "stackoverflow.com")
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        if any((d in netloc for d in _JS_DOMAINS)):
            return FetchPolicy.js_capable()
    except Exception:
        pass
    return FetchPolicy.default()

def hits_from_result(discovery_result) -> tuple:
    """Extract hits from discovery result object."""
    if discovery_result is None:
        return ()
    if hasattr(discovery_result, "hits"):
        return getattr(discovery_result, "hits", ())
    if isinstance(discovery_result, (list, tuple)):
        return discovery_result
    return ()

def generate_rescue_urls(query: str, max_urls: int=8) -> list:
    """Generate lightweight rescue DiscoveryHits for non-domain threat queries.

    Sprint F220C: When bootstrap generates zero URLs (non-domain query),
    and the query appears to be a threat/malware/ransomware/entity search,
    generate rescue candidate hits from static CTI/news search URLs.
    """
    from .public_constants import _RESGUE_SOURCE_CANDIDATES
    if not query or max_urls < 1:
        return []
    if not _is_threat_query_impl(query):
        return []
    hits = []
    for name, base_url in _RESGUE_SOURCE_CANDIDATES[:max_urls]:
        url = f"{base_url}{urllib.parse.quote(query.strip())}"
        hits.append(_make_discovery_hit(query=query, title=f"Rescue: {name}", url=url, snippet=f"Rescue search via {name}: {query}", score=0.7, reason="rescue_candidate", rank=-1, source="rescue"))
    return hits

def _make_discovery_hit(query: str, title: str, url: str, snippet: str, score: float, reason: str, rank: int, source: str) -> Any:
    """Construct a DiscoveryHit (lazy import to avoid circular deps)."""
    from hledac.universal.discovery.base import DiscoveryHit
    return DiscoveryHit(query=query, title=title, url=url, snippet=snippet, score=score, reason=reason, rank=rank, source=source, retrieved_ts=time.time())

def generate_bootstrap_urls(query: str, max_urls: int=5) -> list[str]:
    """Generate deterministic bootstrap URLs for domain/URL queries.

    Bounded: at most max_urls URLs returned.
    No network I/O — pure synchronous URL construction.
    """
    from .public_constants import _BOOTSTRAP_DEFAULT_URLS
    if not query or max_urls < 1:
        return []
    clean_query = query.strip()
    for prefix in ("site:", "domain:", "url:"):
        if clean_query.lower().startswith(prefix):
            clean_query = clean_query[len(prefix):].strip()
            break
    domain = _extract_domain_from_query(clean_query)
    if not domain:
        return []
    paths = _BOOTSTRAP_DEFAULT_URLS[:max_urls]
    urls = []
    for path in paths:
        if path == "/www.":
            urls.append(f"https://www.{domain}")
        elif path:
            urls.append(f"https://{domain}{path}")
        else:
            urls.append(f"https://{domain}")
    return urls
_MAX_SEED_CONTEXT_BOOTSTRAP: int = 10

def generate_seed_context_bootstrap_urls(seed_context: Any, max_candidates: int=_MAX_SEED_CONTEXT_BOOTSTRAP) -> list[str]:
    """Generate deterministic bootstrap URLs from NonfeedSeedContext.

    Bounded: at most max_candidates URLs returned.
    Fail-safe: returns empty list for None seed_context or parse errors.
    """
    if not seed_context or max_candidates < 1:
        return []
    urls: list[str] = []
    _has_domains = bool(getattr(seed_context, "domains", ()))
    _has_urls = bool(getattr(seed_context, "urls", ()))
    _both_sources = _has_domains and _has_urls
    if _both_sources:
        _max_per_source = (max_candidates + 1) // 2
    else:
        _max_per_source = max_candidates
    if _has_domains:
        for domain in list(getattr(seed_context, "domains", ()))[:_max_per_source]:
            if len(urls) >= max_candidates:
                break
            if not domain or "." not in domain:
                continue
            try:
                domain = domain.lower().strip()
                if not domain.startswith(("http://", "https://")):
                    urls.append(f"https://{domain}")
                else:
                    urls.append(domain)
            except Exception:
                continue
    if _has_urls:
        for url in list(getattr(seed_context, "urls", ()))[:_max_per_source]:
            if len(urls) >= max_candidates:
                break
            if not url:
                continue
            try:
                url_str = str(url).strip()
                if not url_str.startswith(("http://", "https://")):
                    continue
                urls.append(url_str)
            except Exception:
                continue
    return urls[:max_candidates]
_PUBLIC_BOOTSTRAP_SEARCH_ENGINES: tuple[str, ...] = ("duckduckgo", "yahoo", "bing", "startpage")
_MAX_KEYWORD_BOOTSTRAP_URLS: int = 10

async def generate_keyword_bootstrap_urls(query: str, max_urls: int=_MAX_KEYWORD_BOOTSTRAP_URLS) -> list:
    """Keyword-based search engine bootstrap — falls back through multiple engines."""
    _ensure_discovery_patched()
    if not query or not query.strip():
        return []
    for engine in _PUBLIC_BOOTSTRAP_SEARCH_ENGINES:
        try:
            raw_results = await _async_search_multi_engine_var.get()(query, max_results=max_urls)
            if not raw_results:
                continue
            hits = []
            for i, item in enumerate(raw_results[:max_urls]):
                url = item.get("url", "") if isinstance(item, dict) else getattr(item, "url", "")
                title = item.get("title", "") if isinstance(item, dict) else getattr(item, "title", "")
                snippet = item.get("snippet", "") if isinstance(item, dict) else getattr(item, "snippet", "")
                if not url:
                    continue
                hits.append(_make_discovery_hit(query=query, title=title or f"{engine.capitalize()} result {i + 1}", url=url, snippet=snippet or f"Keyword bootstrap via {engine}: {query}", score=0.75, reason=f"keyword_bootstrap_{engine}", rank=i, source=engine))
            if hits:
                return hits
        except Exception:
            continue
    return []

def _extract_domain_from_query(query: str) -> str | None:
    """Extract domain from query (plain domain, URL, or mixed OSINT query)."""
    if not query:
        return None
    candidates = [query]
    if " " in query or "\t" in query:
        first_token = query.strip().split()[0]
        if first_token and first_token != query:
            candidates.append(first_token)
    for candidate in candidates:
        q = candidate
        for prefix in ("site:", "domain:", "url:"):
            if q.lower().startswith(prefix):
                q = q[len(prefix):]
                break
        q = q.rstrip("/")
        if "/" in q and "://" in q:
            try:
                parsed = urllib.parse.urlparse(q)
                host = parsed.netloc or parsed.path.split("/")[0]
                if host:
                    q = host
            except Exception:
                pass
        if ":" in q:
            q = q.rsplit(":", 1)[0]
        if q.lower().startswith("www."):
            q = q[4:]
        if q.startswith("*."):
            q = q[2:]
        if not q or "." not in q:
            continue
        if re.match("^\\d{1,3}(\\.\\d{1,3}){3}$", q):
            continue
        if not re.match("^[a-zA-Z0-9.\\-]+$", q):
            continue
        tld = q.rsplit(".", 1)[-1] if "." in q else ""
        if len(tld) < 2:
            continue
        return q.lower()
    return None

def _extract_provider_surface(discovery_result, selected_out: list, skipped_out: list, stub_out: list, errors_out: list, timeout_count_out: list, import_error_count_out: list, empty_reason_out: list) -> None:
    """Classify discovery hits into selected/skipped/stub/error buckets.

    DI seam: reads _ASYNC_FETCH_PUBLIC_TEXT, _SYNC_MATCH_TEXT via
    _ensure_patched() from the fetch module.
    """
    from .public_constants import _filter_public_noise
    if discovery_result is None:
        errors_out.append("discovery_result is None")
        return
    hits = hits_from_result(discovery_result)
    selected_hits = []
    stub_hits = []
    skipped_hits = []
    for hit in hits:
        reason = getattr(hit, "reason", "") or ""
        score = getattr(hit, "score", 0.0)
        url = getattr(hit, "url", "") or ""
        if score <= 0.05:
            skipped_hits.append(hit)
            continue
        if reason in ("rescue_candidate", "bootstrap_root", "bootstrap_www", "bootstrap_security_txt", "bootstrap_robots", "bootstrap_sitemap", "seed_context_domain", "seed_context_url"):
            stub_hits.append(hit)
            continue
        selected_hits.append(hit)
    query = getattr(discovery_result, "query", "") if discovery_result else ""
    is_threat = _is_threat_query_impl(query) if query else False
    filtered, _ = _filter_public_noise(selected_hits, is_threat)
    selected_out.extend(filtered)
    skipped_out.extend(skipped_hits)
    stub_out.extend(stub_hits)
_ct_scanner_var: contextvars.ContextVar[Any] = contextvars.ContextVar("_ct_scanner_var", default=None)

def _patch_ct_scanner(get_subdomains_fn: Any) -> None:
    """DI: override the CT subdomain scanner function."""
    _ct_scanner_var.set(get_subdomains_fn)

def _ensure_ct_scanner_patched() -> None:
    """Ensure CT scanner is patched; fall back to _get_subdomains if not."""
    if _ct_scanner_var.get() is None:
        _ct_scanner_var.set(_get_subdomains)

async def _get_subdomains(domain: str, async_session: Any=None) -> list[str]:
    """Default CT subdomain lookup via _CTLogScanner."""
    try:
        from hledac.universal.network.ct_log_scanner import _CTLogScanner
        scanner = _CTLogScanner()
        return await scanner.get_subdomains(domain, async_session=async_session)
    except Exception:
        return []

class _CTHit:
    """CT-synthesized discovery hit."""

    __slots__ = ("url", "rank")

    def __init__(self, url: str, rank: int):
        self.url = url
        self.rank = rank

async def _inject_ct_subdomain_hits(hits: tuple, query: str) -> tuple:
    """Inject CT subdomain hits into the discovery hits tuple.

    Bounded: at most _CT_SUBDOMAIN_BOUND subdomains injected.
    Fail-safe: returns original hits tuple on any error.
    """
    from .public_constants import _CT_QUERY_IS_DOMAIN_RE, _CT_SUBDOMAIN_BOUND
    _ensure_ct_scanner_patched()
    if not _CT_QUERY_IS_DOMAIN_RE.match(query):
        return hits
    try:
        domain = _extract_domain_from_query(query)
        if not domain:
            return hits
    except Exception:
        return hits
    try:
        subdomains = await _ct_scanner_var.get()(domain)
    except Exception:
        return hits
    if not subdomains:
        return hits
    ct_hits = []
    for rank, subdomain in enumerate(subdomains[:_CT_SUBDOMAIN_BOUND]):
        url = f"https://{subdomain}"
        ct_hits.append(_CTHit(url=url, rank=rank))
    return ((*hits, *ct_hits), None, None, None, None, None, None, None, None)

def _query_looks_like_domain_for_cc(query: str) -> bool:
    """Check if query looks like a domain suitable for CommonCrawl lookup."""
    from .public_constants import _CC_QUERY_IS_DOMAIN_RE
    return bool(_CC_QUERY_IS_DOMAIN_RE.match(query))

class _CCHit:
    """CommonCrawl-synthesized discovery hit."""

    __slots__ = ("url", "title", "snippet", "rank")

    def __init__(self, url: str, title: str, snippet: str, rank: int):
        self.url = url
        self.title = title
        self.snippet = snippet
        self.rank = rank

class _MinimalStealth:
    """Minimal stealth client for CommonCrawl adapter."""

    __slots__ = ()

    async def get(self, url: str) -> str:
        try:
            from hledac.universal.network.session_runtime import async_get_httpx_session
            session = await async_get_httpx_session()
            resp = await session.get(url, timeout=httpx.Timeout(total=10.0))
            return resp.text
        except Exception:
            return ""
_CC_SCANNER_LOOKUP: Any = None

def _get_cc_adapter() -> Any:
    """Lazy-initialize CommonCrawl adapter."""
    global _CC_SCANNER_LOOKUP
    if _CC_SCANNER_LOOKUP is None:
        from hledac.universal.tools.commoncrawl_adapter import CommonCrawlAdapter
        _CC_SCANNER_LOOKUP = CommonCrawlAdapter(stealth=_MinimalStealth())
    return _CC_SCANNER_LOOKUP

async def _inject_commoncrawl_hits(hits: tuple, query: str) -> tuple:
    """Inject CommonCrawl hits for domain-like queries.

    Bounded: at most 10 CC hits injected.
    Fail-safe: returns original hits tuple on any error.
    """
    if not _query_looks_like_domain_for_cc(query):
        return hits
    try:
        domain = _extract_domain_from_query(query)
        if not domain:
            return hits
    except Exception:
        return hits
    try:
        adapter = _get_cc_adapter()
        raw_hits = await adapter.lookup(domain, query)
    except Exception:
        return hits
    if not raw_hits:
        return hits
    cc_hits = []
    for rank, item in enumerate(raw_hits[:10]):
        url = item.get("url", "") if isinstance(item, dict) else getattr(item, "url", "")
        title = item.get("title", "") if isinstance(item, dict) else getattr(item, "title", "")
        snippet = item.get("snippet", "") if isinstance(item, dict) else getattr(item, "snippet", "")
        if url:
            cc_hits.append(_CCHit(url=url, title=title, snippet=snippet, rank=rank))
    return (*hits, *cc_hits)
_ONION_HIT_MAX: int = 5
_ONION_CIRCUIT_FAIL_LIMIT: int = 3
_onion_circuit_failures_var: contextvars.ContextVar[int] = contextvars.ContextVar("_onion_circuit_failures_var", default=0)

def _onion_circuit_is_open() -> bool:
    """Check if Tor circuit is available."""
    return _onion_circuit_failures_var.get() < _ONION_CIRCUIT_FAIL_LIMIT

def _onion_circuit_record_failure() -> None:
    """Record Tor circuit failure."""
    _onion_circuit_failures_var.set(_onion_circuit_failures_var.get() + 1)

async def _inject_onion_hits(hits: tuple, query: str, store: Any) -> int:
    """Inject onion hits for dark web queries.

    Returns count of new onion hits injected.
    Fail-safe: returns 0 on any error.
    """
    from .public_constants import _is_threat_query as _is_threat_q
    if not _is_threat_q(query):
        return 0
    if not _onion_circuit_is_open():
        return 0
    try:
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text
        base_url = f"https://ahmia.fi/search/?q={urllib.parse.quote(query)}"
        text = await async_fetch_public_text(url=base_url, timeout_s=10.0, max_bytes=100000)
        if not text:
            _onion_circuit_record_failure()
            return 0
    except Exception:
        return 0
    onion_count = 0
    text_str = text if isinstance(text, str) else ""
    for match in re.finditer('onion[^\\s\\"\']+', text_str):
        url = match.group(0).rstrip("/.,")
        if url.startswith("http") and ".onion" in url:
            hits = (*hits, _CTHit(url=url, rank=-1))
            onion_count += 1
            if onion_count >= _ONION_HIT_MAX:
                break
    return onion_count

class _DiscoveryEngine:
    """Discovery loop engine — orchestrates all discovery lanes.

    Runs: rescue → bootstrap → seed_context → CT → CC → onion → academic.
    Produces classified hits + telemetry.
    """

    __slots__ = tuple(("ct_subdomains_fn", "discovery_telemetry", "empty_reasons", "errors", "import_error_count", "public_bootstrap_enabled", "query", "seed_context", "selected_hits", "skipped_hits", "stub_hits", "timeout_count"))

    def __init__(self, query: str, public_bootstrap_enabled: bool=False, seed_context: Any | None=None, ct_subdomains_fn: Any | None=None):
        self.query = query
        self.public_bootstrap_enabled = public_bootstrap_enabled
        self.seed_context = seed_context
        self.ct_subdomains_fn = ct_subdomains_fn
        self.selected_hits: list = []
        self.skipped_hits: list = []
        self.stub_hits: list = []
        self.errors: list = []
        self.timeout_count: int = 0
        self.import_error_count: int = 0
        self.empty_reasons: list = []
        self.discovery_telemetry: dict = {}

    async def run(self, uma_state: str) -> tuple:
        """Run full discovery pipeline.

        Returns: (hits, discovery_result, discovery_error,
                  discovery_error_type, discovery_elapsed_s,
                  discovery_attempted, discovery_telemetry,
                  academic_findings_count, ct_injected,
                  cc_injected, onion_findings_count,
                  pastebin_findings_count, github_secrets_count)
        """
        from .public_constants import _filter_public_noise
        _ensure_discovery_patched()
        _ensure_ct_scanner_patched()
        start = time.monotonic()
        discovery_result = None
        discovery_error = None
        discovery_error_type = None
        discovery_elapsed_s = 0.0
        discovery_attempted = False
        rescue_hits = generate_rescue_urls(self.query, max_urls=8)
        bootstrap_urls = generate_bootstrap_urls(self.query)
        seed_urls: list[str] = []
        if self.seed_context:
            seed_urls = generate_seed_context_bootstrap_urls(self.seed_context)
        keyword_hits = []
        if not bootstrap_urls and (not seed_urls) and (not rescue_hits):
            discovery_attempted = True
            try:
                keyword_hits = await generate_keyword_bootstrap_urls(self.query)
            except Exception as e:
                discovery_error = str(e)
                discovery_error_type = type(e).__name__
        all_hits = []
        for h in rescue_hits:
            all_hits.append(h)
        for url in bootstrap_urls:
            all_hits.append(_make_discovery_hit(query=self.query, title="Bootstrap", url=url, snippet=f"Bootstrap: {url}", score=0.5, reason="bootstrap", rank=-1, source="bootstrap"))
        for url in seed_urls:
            all_hits.append(_make_discovery_hit(query=self.query, title="SeedContext", url=url, snippet=f"SeedContext: {url}", score=0.5, reason="seed_context", rank=-1, source="seed_context"))
        for h in keyword_hits:
            all_hits.append(h)
        selected = []
        skipped = []
        stub = []
        errors = []
        timeout_count = 0
        import_error_count = 0
        empty_reasons = []
        is_threat = _is_threat_query_impl(self.query)
        filtered, rejected = _filter_public_noise(all_hits, is_threat)
        for hit in filtered:
            reason = getattr(hit, "reason", "") or ""
            score = getattr(hit, "score", 0.0)
            if score <= 0.05:
                skipped.append(hit)
            elif reason in ("rescue_candidate", "bootstrap_root", "bootstrap_www", "bootstrap_security_txt", "bootstrap_robots", "bootstrap_sitemap", "seed_context_domain", "seed_context_url"):
                stub.append(hit)
            else:
                selected.append(hit)
        from .public_constants import _CT_QUERY_IS_DOMAIN_RE
        ct_injected = 0
        if self.query and _CT_QUERY_IS_DOMAIN_RE.match(self.query):
            try:
                selected, *_ = await _inject_ct_subdomain_hits(tuple(selected), self.query)
                ct_injected = len(selected)
            except Exception:
                pass
        cc_injected = 0
        if _query_looks_like_domain_for_cc(self.query):
            try:
                selected, *_ = await _inject_commoncrawl_hits(tuple(selected), self.query)
                cc_injected = len(selected)
            except Exception:
                pass
        onion_findings_count = 0
        discovery_elapsed_s = time.monotonic() - start
        academic_findings_count = 0
        pastebin_findings_count = 0
        github_secrets_count = 0
        return (tuple(selected), discovery_result, discovery_error, discovery_error_type, discovery_elapsed_s, discovery_attempted, self.discovery_telemetry, academic_findings_count, ct_injected, cc_injected, onion_findings_count, pastebin_findings_count, github_secrets_count)

async def limited_academic_search(query: str, uma_state: str, telemetry: dict) -> int:
    """Run bounded academic/pastebin/github discovery lanes.

    Extracted from the nested position inside _DiscoveryEngine.run().
    Returns count of academic findings found.
    """
    ACADEMIC_ENABLED = False
    try:
        from hledac.universal.discovery.academic import ACADEMIC_ENABLED, search_all_academic
    except ImportError:
        ACADEMIC_ENABLED = False
    academic_count = 0
    if ACADEMIC_ENABLED:
        try:
            academic_hits = await search_all_academic(query, max_results_per_source=10)
            for hit in academic_hits:
                telemetry["academic_hits"] = telemetry.get("academic_hits", 0) + 1
                academic_count += 1
        except Exception:
            pass
    return academic_count

async def run_tot_with_timeout(hypo: str, timeout_s: float=15.0) -> str:
    """Run tree-of-thought reasoning on a hypothesis with hard timeout.

    Bounded: timeout_s caps the TOT reasoning time.
    Fail-safe: returns empty string on timeout or error.
    """
    try:
        tot_layer = None
        try:
            from hledac.universal.tot_integration import TotIntegrationLayer
            tot_layer = TotIntegrationLayer()
        except ImportError:
            return ""
        async with asyncio.timeout(timeout_s):
            result = await tot_layer.solve_with_tot(hypo)
        return result or ""
    except (TimeoutError, Exception):
        return ""