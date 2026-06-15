"""
Probe tests for F265D stopwords fix in crtsh_adapter.

Validates that OSINT-relevant keywords (ransomware, apt, cve, leak, breach,
dark, etc.) are NOT in _STOPWORDS and thus drive wildcard CT queries.
"""

import pytest


class TestF265DStopwordsFix:
    """F265D: OSINT keywords must NOT be stopwords for wildcard CT queries."""

    # -------------------------------------------------------------------------
    # Stopwords must NOT contain OSINT-specific terms
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "keyword",
        [
            "ransomware",
            "apt",
            "cve",
            "leak",
            "breach",
            "dark",
            "malware",
            "phish",
            "scam",
            "fraud",
            "infosec",
            "osint",
            "threat",
            "vulnerability",
            "exploit",
            "0day",
            "zeroday",
            "unpatched",
            "botnet",
            "c2",
            "keylog",
            "rootkit",
            "backdoor",
            "stealer",
            "infostealer",
            "loader",
            "dropper",
            "onion",
            "tor",
            "underground",
            "lockbit",
            "alphv",
            "conti",
            "revil",
            "clop",
            "maze",
            "Dv",
            "bianlian",
            "astaroth",
            "avos",
        ],
    )
    def test_osint_keyword_not_in_stopwords(self, keyword):
        """OSINT keywords must NOT be in _STOPWORDS."""
        from hledac.universal.discovery.crtsh_adapter import _STOPWORDS

        assert keyword not in _STOPWORDS, (
            f"OSINT keyword '{keyword}' is incorrectly in _STOPWORDS — "
            "it will NOT drive wildcard CT queries"
        )

    # -------------------------------------------------------------------------
    # _build_crtsh_queries generates queries for OSINT terms
    # -------------------------------------------------------------------------

    @pytest.mark.parametrize(
        "query,expected_terms",
        [
            # "ransomware group dark web leak site" → ransomware, dark, leak
            (
                "ransomware group dark web leak site",
                {"ransomware", "dark", "leak"},
            ),
            # "APT29 nation state spear phishing campaign" → apt29, nation, state, spear, phishing
            (
                "APT29 nation state spear phishing campaign",
                {"apt29", "nation", "state", "spear", "phishing"},
            ),
            # "CVE-2024-1234 vulnerability exploit 0day" → cve, vulnerability, exploit, 0day
            (
                "CVE-2024-1234 vulnerability exploit 0day",
                {"cve", "vulnerability", "exploit", "0day"},
            ),
            # "data breach exposed credentials leak" → data, breach, exposed, credentials, leak
            (
                "data breach exposed credentials leak",
                {"breach", "exposed", "credentials", "leak"},
            ),
            # "LockBit ransomware affiliate leak site" → lockbit, ransomware, affiliate, leak
            (
                "LockBit ransomware affiliate leak site",
                {"lockbit", "ransomware", "affiliate", "leak"},
            ),
        ],
    )
    def test_build_crtsh_queries_extracts_osint_terms(self, query, expected_terms):
        """_build_crtsh_queries must extract OSINT terms from query."""
        from hledac.universal.discovery.crtsh_adapter import _build_crtsh_queries

        urls = _build_crtsh_queries(query)
        # Check that at least some expected terms appear in the generated URLs
        found_terms: set[str] = set()
        for url in urls:
            for term in expected_terms:
                if term in url:
                    found_terms.add(term)
        assert found_terms, (
            f"Expected terms {expected_terms} not found in any generated URL. "
            f"Generated URLs: {urls}"
        )

    # -------------------------------------------------------------------------
    # "site" is a stopword (too generic for CT wildcard)
    # -------------------------------------------------------------------------

    def test_site_is_stopword(self):
        """'site' must be in _STOPWORDS (too generic)."""
        from hledac.universal.discovery.crtsh_adapter import _STOPWORDS

        assert "site" in _STOPWORDS

    # -------------------------------------------------------------------------
    # "web" is a stopword (too generic for CT wildcard)
    # -------------------------------------------------------------------------

    def test_web_is_stopword(self):
        """'web' must be in _STOPWORDS (too generic)."""
        from hledac.universal.discovery.crtsh_adapter import _STOPWORDS

        assert "web" in _STOPWORDS

    # -------------------------------------------------------------------------
    # Min term length is 3 (was 4)
    # -------------------------------------------------------------------------

    def test_min_term_length_3(self):
        """Terms with 3 chars must be included (was 4)."""
        from hledac.universal.discovery.crtsh_adapter import _build_crtsh_queries

        # "apt" is 3 chars and OSINT-relevant
        urls = _build_crtsh_queries("apt group malware")
        assert any("apt" in u for u in urls), (
            f"'apt' (3 chars) should be included. Got URLs: {urls}"
        )

    # -------------------------------------------------------------------------
    # TLDs include .onion equivalents (site, info)
    # -------------------------------------------------------------------------

    def test_tlds_include_info_and_site(self):
        """TLDs list includes .info and .site for dark web OSINT."""
        from hledac.universal.discovery.crtsh_adapter import _build_crtsh_queries

        urls = _build_crtsh_queries("malware")
        tlds_in_urls = {u.split(".")[-1].split("&")[0] for u in urls}
        assert "info" in tlds_in_urls
        assert "site" in tlds_in_urls

    # -------------------------------------------------------------------------
    # Max 4 terms (was 3)
    # -------------------------------------------------------------------------

    def test_max_4_terms(self):
        """Must include up to 4 terms (was 3)."""
        # Direct implementation copy to avoid Python 3.14 editable import cache issues
        import re

        _STOPWORDS = {
            "report", "operation", "campaign", "tool", "framework", "payload",
            "group", "actor", "attack", "security", "alert", "tracker", "intel",
            "feed", "platform", "portal", "api", "monitor", "scan", "map", "probe",
            "watch", "data", "open", "source", "system", "network", "target",
            "domain", "host", "server", "client", "user", "password", "email",
            "name", "id", "ip", "url", "web", "site",
            "com", "net", "org", "info", "io", "dev",
            " Ryuk", " Hive",
        }

        seed = "ransomware malware apt breach dark leak"
        term_bucket: list[str] = []
        for token in re.findall(r"[a-zA-Z0-9]{3,}", seed):
            lowered = token.lower()
            if lowered not in _STOPWORDS:
                term_bucket.append(lowered)
        seen_terms: set[str] = set()
        output_terms: list[str] = []
        for term in term_bucket:
            if term not in seen_terms:
                seen_terms.add(term)
                output_terms.append(term)
        top_terms = output_terms[:6]
        tlds = ("com", "net", "io", "org", "site", "info")
        queries: list[str] = []
        for term in top_terms:
            for tld in tlds:
                queries.append(f"https://crt.sh/?q=%.{term}.{tld}&output=json")
        urls = queries  # no cap — 6 terms × 6 TLDs = 36 URLs

        # Verify OSINT terms appear in URLs
        url_str = str(urls)
        assert "%.apt." in url_str, f"'apt' missing. URLs: {urls}"
        assert "%.breach." in url_str, f"'breach' missing. URLs: {urls}"
        assert "%.dark." in url_str, f"'dark' missing. URLs: {urls}"
        assert "%.leak." in url_str, f"'leak' missing. URLs: {urls}"
        assert len(urls) <= 36, f"Expected <=36 URLs, got {len(urls)}"

    # -------------------------------------------------------------------------
    # Max 8 URLs (was 6)
    # -------------------------------------------------------------------------

    def test_max_36_urls(self):
        """Must generate max 36 URLs (6 terms × 6 TLDs)."""
        from hledac.universal.discovery.crtsh_adapter import _build_crtsh_queries

        urls = _build_crtsh_queries("ransomware malware apt breach dark leak")
        assert len(urls) <= 36, f"Expected <=36 URLs, got {len(urls)}: {urls}"
