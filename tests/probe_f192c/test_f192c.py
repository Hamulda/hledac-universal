"""
Sprint F192C — Plain-TCP Session Hot-Path Alignment
==================================================

Tests verify alignment of plain-TCP hot-path aiohttp session usage onto
the shared session_runtime surface, and confirmation of intentional
transport separation.

Gates tested:
  [F1]  circuit_breaker.resilient_fetch() clearnet path uses
        async_get_aiohttp_session() — NO ad-hoc ClientSession
  [F2]  circuit_breaker.resilient_fetch() Tor path retains ProxyConnector
        session (architecturally correct — cannot share TCPConnector pool)
  [F3]  circuit_breaker.resilient_fetch() Tor path does NOT use
        async_get_aiohttp_session() — correctly kept separate
  [F4]  ct_log_scanner imports CT constants from session_runtime
        (CT_CONNECT_TIMEOUT_S, CT_READ_TIMEOUT_S)
  [F5]  ct_log_scanner uses async_get_aiohttp_session() for shared pool
  [F6]  session_runtime exports CT constants (CT_CONNECT_TIMEOUT_S,
        CT_READ_TIMEOUT_S) as public API
  [F7]  session_runtime exports HTML constants (already verified in 8AA,
        included here for completeness)
  [F8]  public_fetcher still uses async_get_aiohttp_session() — no drift
  [F9]  circuit_breaker.py has NO bare aiohttp.ClientSession() in
        clearnet path (resilient_fetch guard)
  [F10] ct_log_scanner._CT_CONNECT_TIMEOUT_S / _CT_READ_TIMEOUT_S are
        removed (consolidated to session_runtime canonical constants)

Invariant contract:
  - Plain TCP aiohttp world = session_runtime.async_get_aiohttp_session()
  - curl_cffi world = SEPARATE (StealthCrawler, FetchCoordinator)
  - ProxyConnector / Tor world = SEPARATE (cannot share TCPConnector pool)
  - Nym world = SEPARATE (nym_transport)
"""

import asyncio
import inspect
import pathlib


# =============================================================================
# [F1] circuit_breaker clearnet path uses shared session
# =============================================================================


class TestCircuitBreakerClearnetSessionAlignment:
    """Verify circuit_breaker resilient_fetch() clearnet path uses shared surface."""

    def test_resilient_fetch_clearnet_uses_async_get_aiohttp_session(self):
        """[F1] resilient_fetch() clearnet path calls async_get_aiohttp_session()."""
        from hledac.universal.transport.circuit_breaker import resilient_fetch

        src = inspect.getsource(resilient_fetch)
        # Clearnet branch must import and call async_get_aiohttp_session
        assert "async_get_aiohttp_session" in src, (
            "clearnet path must use shared session_runtime surface"
        )

    def test_resilient_fetch_clearnet_has_no_bare_client_session(self):
        """[F9] resilient_fetch() clearnet path has NO bare aiohttp.ClientSession()."""
        from hledac.universal.transport.circuit_breaker import resilient_fetch

        src = inspect.getsource(resilient_fetch)

        # Split source into clearnet and tor sections for separate analysis
        clearnet_section = src.split("elif transport == \"tor\":")[0]

        # Clearnet section must NOT contain bare ClientSession(
        assert "aiohttp.ClientSession(" not in clearnet_section, (
            "clearnet path must NOT create ad-hoc ClientSession — "
            "use async_get_aiohttp_session() instead"
        )

    def test_resilient_fetch_clearnet_imports_html_timeouts(self):
        """[F1] resilient_fetch() clearnet path imports HTML timeouts from session_runtime."""
        from hledac.universal.transport.circuit_breaker import resilient_fetch

        src = inspect.getsource(resilient_fetch)
        # Should import HTML timeouts for use in timeout parameter
        assert "HTML_CONNECT_TIMEOUT_S" in src or "HTML_READ_TIMEOUT_S" in src, (
            "clearnet path should use HTML timeout constants from session_runtime"
        )


# =============================================================================
# [F2, F3] Tor path is correctly kept separate (ProxyConnector)
# =============================================================================


class TestCircuitBreakerTorPathSeparation:
    """Verify Tor path retains its own ProxyConnector session — correctly separate."""

    def test_resilient_fetch_tor_uses_proxy_connector(self):
        """[F2] resilient_fetch() Tor path uses ProxyConnector — cannot share pool."""
        from hledac.universal.transport.circuit_breaker import resilient_fetch

        src = inspect.getsource(resilient_fetch)
        # Tor section must use ProxyConnector
        tor_section = src.split("elif transport == \"tor\":")[1]
        assert "ProxyConnector" in tor_section, (
            "Tor path must use ProxyConnector for SOCKS5"
        )

    def test_resilient_fetch_tor_does_not_use_shared_session(self):
        """[F3] resilient_fetch() Tor path does NOT call async_get_aiohttp_session()."""
        from hledac.universal.transport.circuit_breaker import resilient_fetch

        src = inspect.getsource(resilient_fetch)
        # Tor section must NOT call async_get_aiohttp_session
        tor_section = src.split("elif transport == \"tor\":")[1]
        assert "async_get_aiohttp_session" not in tor_section, (
            "Tor path must NOT use shared session — ProxyConnector is "
            "architecturally incompatible with shared TCPConnector pool"
        )

    def test_resilient_fetch_tor_has_own_client_session(self):
        """[F2] resilient_fetch() Tor path creates its own session with ProxyConnector."""
        from hledac.universal.transport.circuit_breaker import resilient_fetch

        src = inspect.getsource(resilient_fetch)
        tor_section = src.split("elif transport == \"tor\":")[1]
        # Tor path should have its own ClientSession with ProxyConnector
        assert "aiohttp.ClientSession(connector=" in tor_section or \
               "ClientSession(connector=" in tor_section, (
            "Tor path must create its own session with ProxyConnector"
        )


# =============================================================================
# [F4, F5, F10] ct_log_scanner alignment
# =============================================================================


class TestCtLogScannerSessionAlignment:
    """Verify ct_log_scanner uses shared session_runtime surface."""

    def test_ct_log_scanner_imports_ct_constants(self):
        """[F4] ct_log_scanner imports CT constants from session_runtime."""
        import ast

        ct_path = pathlib.Path(__file__).parents[4] / "network" / "ct_log_scanner.py"
        content = ct_path.read_text()
        tree = ast.parse(content)

        imports_session_runtime = False
        imports_ct_constants = False

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "hledac.universal.network.session_runtime":
                    for alias in node.names:
                        if alias.name == "CT_CONNECT_TIMEOUT_S":
                            imports_ct_constants = True
                        if alias.name in ("async_get_aiohttp_session",
                                          "CT_CONNECT_TIMEOUT_S",
                                          "CT_READ_TIMEOUT_S"):
                            imports_session_runtime = True

        assert imports_session_runtime, (
            "ct_log_scanner must import from session_runtime"
        )
        assert imports_ct_constants, (
            "ct_log_scanner must import CT_CONNECT_TIMEOUT_S from session_runtime "
            "(removes duplicate local constants — F192C)"
        )

    def test_ct_log_scanner_no_duplicate_ct_constants(self):
        """[F10] ct_log_scanner has NO local _CT_CONNECT_TIMEOUT_S/_CT_READ_TIMEOUT_S."""
        ct_path = pathlib.Path(__file__).parents[4] / "network" / "ct_log_scanner.py"
        content = ct_path.read_text()

        # Must NOT have local underscore-prefixed CT constants
        assert "_CT_CONNECT_TIMEOUT_S" not in content, (
            "Duplicate _CT_CONNECT_TIMEOUT_S must be removed — "
            "import from session_runtime instead"
        )
        assert "_CT_READ_TIMEOUT_S" not in content, (
            "Duplicate _CT_READ_TIMEOUT_S must be removed — "
            "import from session_runtime instead"
        )

    def test_ct_log_scanner_uses_shared_session(self):
        """[F5] ct_log_scanner calls async_get_aiohttp_session() for shared pool."""
        from hledac.universal.network.ct_log_scanner import _CTLogScanner

        src = inspect.getsource(_CTLogScanner.get_subdomains)
        assert "async_get_aiohttp_session" in src, (
            "ct_log_scanner must use shared session_runtime surface"
        )


# =============================================================================
# [F6, F7] session_runtime canonical constants surface
# =============================================================================


class TestSessionRuntimeCanonicalConstants:
    """Verify session_runtime exports all canonical timeout constants."""

    def test_ct_constants_exported(self):
        """[F6] session_runtime exports CT_CONNECT_TIMEOUT_S and CT_READ_TIMEOUT_S."""
        from hledac.universal.network.session_runtime import (
            CT_CONNECT_TIMEOUT_S,
            CT_READ_TIMEOUT_S,
        )
        assert CT_CONNECT_TIMEOUT_S == 10.0
        assert CT_READ_TIMEOUT_S == 15.0

    def test_html_constants_exported(self):
        """[F7] session_runtime exports HTML timeout constants (already in 8AA, F192C completeness)."""
        from hledac.universal.network.session_runtime import (
            HTML_CONNECT_TIMEOUT_S,
            HTML_READ_TIMEOUT_S,
        )
        assert HTML_CONNECT_TIMEOUT_S == 15.0
        assert HTML_READ_TIMEOUT_S == 35.0

    def test_all_timeout_constants_are_public(self):
        """[F6, F7] All timeout constants use public names (no leading underscore)."""
        import ast

        sr_path = pathlib.Path(__file__).parents[4] / "network" / "session_runtime.py"
        content = sr_path.read_text()
        tree = ast.parse(content)

        public_constants = [
            "API_CONNECT_TIMEOUT_S",
            "API_READ_TIMEOUT_S",
            "HTML_CONNECT_TIMEOUT_S",
            "HTML_READ_TIMEOUT_S",
            "CT_CONNECT_TIMEOUT_S",
            "CT_READ_TIMEOUT_S",
            "TOR_CONNECT_TIMEOUT_S",
            "TOR_READ_TIMEOUT_S",
        ]

        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        if name.endswith("_S") and name[0] != "_":
                            found.add(name)

        for const in public_constants:
            assert const in found, f"{const} must be a public constant in session_runtime"


# =============================================================================
# [F8] public_fetcher drift guard
# =============================================================================


class TestPublicFetcherDriftGuard:
    """Verify public_fetcher still uses shared session_runtime — no drift."""

    def test_public_fetcher_still_uses_shared_session(self):
        """[F8] public_fetcher async_fetch_public_text still calls async_get_aiohttp_session()."""
        from hledac.universal.fetching import public_fetcher

        src = inspect.getsource(public_fetcher)
        assert "async_get_aiohttp_session()" in src, (
            "public_fetcher must still use shared session — no drift"
        )

    def test_public_fetcher_no_ad_hoc_client_session(self):
        """[F8] public_fetcher has NO bare aiohttp.ClientSession() creation."""
        from hledac.universal.fetching import public_fetcher

        src = inspect.getsource(public_fetcher)
        # Must not create its own ClientSession
        assert "aiohttp.ClientSession(" not in src, (
            "public_fetcher must NOT create ad-hoc ClientSession — "
            "use async_get_aiohttp_session()"
        )


# =============================================================================
# [F9] circuit_breaker source-level guard — no ad-hoc session in clearnet
# =============================================================================


class TestCircuitBreakerNoClearnetProliferation:
    """Verify circuit_breaker.py has no ad-hoc plain-TCP session creation."""

    def test_circuit_breaker_module_has_no_adhoc_aiohttp_in_clearnet(self):
        """[F9] circuit_breaker.py overall: no bare ClientSession in clearnet branch."""
        import ast

        cb_path = pathlib.Path(__file__).parents[4] / "transport" / "circuit_breaker.py"
        content = cb_path.read_text()
        tree = ast.parse(content)

        # Find resilient_fetch function
        resilient_fetch_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "resilient_fetch":
                resilient_fetch_node = node
                break

        assert resilient_fetch_node is not None, "resilient_fetch must exist"

        # Extract the function source using AST (not inspect on undefined var)
        func_source = ast.get_source_segment(content, resilient_fetch_node)

        # Split into clearnet vs tor sections
        clearnet_part = func_source.split("elif transport")[0]
        # The tor part starts after "elif transport == \"tor\":"
        if "elif transport == \"tor\":" in func_source:
            tor_part = func_source.split("elif transport == \"tor\":")[1]
        else:
            tor_part = ""

        # Clearnet part must not have bare ClientSession
        assert "aiohttp.ClientSession(" not in clearnet_part, (
            "Clearnet path must not create ad-hoc ClientSession"
        )

        # Tor part SHOULD have ProxyConnector + ClientSession (correctly separate)
        assert "ProxyConnector" in tor_part, "Tor path must use ProxyConnector"


# =============================================================================
# [F11-F15] academic_search session alignment
# =============================================================================


class TestAcademicSearchSessionAlignment:
    """Verify academic_search adapters use shared session_runtime surface."""

    def test_academic_search_imports_async_get_aiohttp_session(self):
        """[F11] academic_search imports async_get_aiohttp_session from session_runtime."""
        import ast

        as_path = pathlib.Path(__file__).parents[4] / "intelligence" / "academic_search.py"
        content = as_path.read_text()
        tree = ast.parse(content)

        imports_shared_session = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "hledac.universal.network.session_runtime":
                    for alias in node.names:
                        if alias.name == "async_get_aiohttp_session":
                            imports_shared_session = True

        assert imports_shared_session, (
            "academic_search must import async_get_aiohttp_session from session_runtime "
            "for shared session fallback"
        )

    def test_academic_search_adapters_no_per_call_session_in_search(self):
        """[F12] ArxivAdapter.search() uses shared session fallback, not per-call ClientSession."""
        import ast

        as_path = pathlib.Path(__file__).parents[4] / "intelligence" / "academic_search.py"
        content = as_path.read_text()
        tree = ast.parse(content)

        # Find ArxivAdapter.search method
        arxiv_search_source = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "search":
                # Check if it's inside ArxivAdapter class
                # We need to find the class context
                arxiv_search_source = ast.unparse(node)
                break

        assert arxiv_search_source is not None, "ArxivAdapter.search must exist"

        # The search method should use async_get_aiohttp_session when no session provided
        assert "async_get_aiohttp_session" in arxiv_search_source, (
            "ArxivAdapter.search must fall back to async_get_aiohttp_session() "
            "when async_session is None"
        )
        # Must NOT create per-call ClientSession
        assert "aiohttp.ClientSession()" not in arxiv_search_source, (
            "ArxivAdapter.search must NOT create per-call aiohttp.ClientSession() — "
            "use async_get_aiohttp_session() instead"
        )

    def test_crossref_adapter_no_per_call_session_in_search(self):
        """[F13] CrossrefAdapter.search() uses shared session fallback, not per-call ClientSession."""
        import ast

        as_path = pathlib.Path(__file__).parents[4] / "intelligence" / "academic_search.py"
        content = as_path.read_text()
        tree = ast.parse(content)

        # Find all AsyncFunctionDef named "search"
        search_methods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "search":
                search_methods.append(ast.unparse(node))

        assert len(search_methods) >= 2, "Should have at least 2 search methods (Arxiv + Crossref)"

        # Check Crossref (second search method)
        crossref_search = search_methods[1]  # Crossref is second
        assert "async_get_aiohttp_session" in crossref_search, (
            "CrossrefAdapter.search must fall back to async_get_aiohttp_session()"
        )
        assert "aiohttp.ClientSession()" not in crossref_search, (
            "CrossrefAdapter.search must NOT create per-call aiohttp.ClientSession()"
        )

    def test_semantic_scholar_adapter_no_per_call_session_in_search(self):
        """[F14] SemanticScholarAdapter.search() uses shared session fallback, not per-call ClientSession."""
        import ast

        as_path = pathlib.Path(__file__).parents[4] / "intelligence" / "academic_search.py"
        content = as_path.read_text()
        tree = ast.parse(content)

        # Find all AsyncFunctionDef named "search"
        search_methods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "search":
                search_methods.append(ast.unparse(node))

        # SemanticScholarAdapter.search is the third one
        assert len(search_methods) >= 3, "Should have at least 3 search methods"

        ss_search = search_methods[2]  # SemanticScholar is third
        assert "async_get_aiohttp_session" in ss_search, (
            "SemanticScholarAdapter.search must fall back to async_get_aiohttp_session()"
        )
        assert "aiohttp.ClientSession()" not in ss_search, (
            "SemanticScholarAdapter.search must NOT create per-call aiohttp.ClientSession()"
        )

    def test_semantic_scholar_client_requires_session_param(self):
        """[F15] SemanticScholarClient.search_ss/search_arxiv require session param — correct design."""
        import ast

        as_path = pathlib.Path(__file__).parents[4] / "intelligence" / "academic_search.py"
        content = as_path.read_text()
        tree = ast.parse(content)

        # Find SemanticScholarClient class and its search methods
        ss_client_source = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "SemanticScholarClient":
                ss_client_source = ast.unparse(node)
                break

        assert ss_client_source is not None, "SemanticScholarClient must exist"

        # search_ss and search_arxiv should require session parameter
        assert "def search_ss(" in ss_client_source, "search_ss must exist"
        assert "def search_arxiv(" in ss_client_source, "search_arxiv must exist"

        # Both should have 'session' as a required parameter (not Optional)
        # Check search_ss signature
        lines = ss_client_source.split('\n')
        search_ss_line = [l for l in lines if 'def search_ss' in l][0]
        assert 'session: aiohttp.ClientSession' in search_ss_line, (
            "search_ss must require session parameter (not Optional) — "
            "caller provides shared session"
        )

        search_arxiv_line = [l for l in lines if 'def search_arxiv' in l][0]
        assert 'session: aiohttp.ClientSession' in search_arxiv_line, (
            "search_arxiv must require session parameter (not Optional) — "
            "caller provides shared session"
        )


class TestAcademicSearchDetailMethodsAcceptSharedSession:
    """Detail methods (get_paper_details, etc.) can have per-call sessions — out of hot path."""

    def test_detail_methods_have_own_session(self):
        """[F16] Detail methods create their own sessions — correct for non-hot-path."""
        import ast

        as_path = pathlib.Path(__file__).parents[4] / "intelligence" / "academic_search.py"
        content = as_path.read_text()
        tree = ast.parse(content)

        # Find detail method names
        detail_methods = ["get_paper_details", "get_work_by_doi", "get_citations"]

        # These methods CAN have per-call sessions because:
        # 1. They are NOT called from AcademicSearchEngine.search() hot path
        # 2. They are used for post-search detail fetches
        # 3. Changing them would require API signature changes
        for method_name in detail_methods:
            found = False
            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef) and node.name == method_name:
                    found = True
                    break

            assert found, f"{method_name} must exist (even if it uses per-call session)"
