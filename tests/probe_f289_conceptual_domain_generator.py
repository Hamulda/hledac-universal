"""
F289: Probe tests for conceptual domain generation (no-domain query bootstrap)

Tests:
1. extract_domain_candidates_from_text returns empty for conceptual query
2. generate_conceptual_domain_candidates returns empty when regex found candidates (fast path)
3. _generate_conceptual_domains_mlx is async and bounded
4. MAX_CONCEPTUAL_DOMAINS limit enforced
5. Fails gracefully when MLX unavailable
6. Confidence is 0.5 for MLX-generated candidates
7. DomainCandidate has correct fields
"""

import asyncio

import pytest

from hledac.universal.runtime.nonfeed_candidate_ledger import (
    MAX_CONCEPTUAL_DOMAINS,
    DomainCandidate,
    extract_domain_candidates_from_text,
    generate_conceptual_domain_candidates,
)


class TestExtractDomainCandidatesEmpty:
    """Regex extraction returns empty for conceptual queries (no domains/IPs)."""

    def test_conceptual_query_returns_empty(self):
        """Query with no domain/IP patterns returns zero candidates."""
        query = "ransomware threat intelligence leak dark web exposure"
        candidates = extract_domain_candidates_from_text(
            query, source_family="PUBLIC", min_confidence=0.3
        )
        assert candidates == [], f"Expected empty for conceptual query, got {candidates}"

    def test_keyword_only_query_returns_empty(self):
        """Pure keyword query returns zero candidates."""
        query = "data breach incident response playbook"
        candidates = extract_domain_candidates_from_text(
            query, source_family="PUBLIC", min_confidence=0.3
        )
        assert candidates == []

    def test_query_with_domain_returns_candidate(self):
        """Query containing an actual domain returns a candidate."""
        query = "evil.com ransomware operations"
        candidates = extract_domain_candidates_from_text(
            query, source_family="PUBLIC", min_confidence=0.3
        )
        assert candidates, "Expected at least one domain candidate"
        assert any("evil.com" in c.domain for c in candidates), "Should extract evil.com"


class TestGenerateConceptualDomainCandidatesFastPath:
    """generate_conceptual_domain_candidates fast path: skip MLX when regex found candidates."""

    def test_returns_empty_when_regex_found_candidates(self, monkeypatch):
        """Fast path: returns [] immediately if regex extraction succeeded."""
        call_count = 0

        async def mock_mlx(_query):
            nonlocal call_count
            call_count += 1
            return []

        # Patch the internal MLX function
        import hledac.universal.runtime.nonfeed_candidate_ledger as ledger

        monkeypatch.setattr(ledger, "_generate_conceptual_domains_mlx", mock_mlx)

        query = "evil.com ransomware"  # has domain — regex should find it
        result = asyncio.run(generate_conceptual_domain_candidates(query))
        assert result == [], f"Expected [] (regex found candidates), got {result}"
        assert call_count == 0, "MLX should NOT be called when regex found candidates"


class TestGenerateConceptualDomainCandidatesBounded:
    """MLX generator is async, bounded, and fail-safe."""

    @pytest.mark.asyncio
    async def test_is_async_function(self):
        """generate_conceptual_domain_candidates is a coroutine."""
        import inspect

        assert inspect.iscoroutinefunction(generate_conceptual_domain_candidates)

    @pytest.mark.asyncio
    async def test_returns_empty_for_short_query(self):
        """Query too short returns empty without calling MLX."""
        result = await generate_conceptual_domain_candidates("abc")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_query(self):
        """Empty/None query returns empty."""
        result = await generate_conceptual_domain_candidates("")
        assert result == []
        result2 = await generate_conceptual_domain_candidates(None)  # type: ignore
        assert result2 == []

    @pytest.mark.asyncio
    async def test_fails_gracefully_when_mlx_unavailable(self, monkeypatch):
        """MLX unavailable → returns [], never raises."""
        # Patch DeepHermes3Engine at class level so engine.initialize() fails
        import hledac.universal.brain.deephermes3_engine as dhe

        class FailingEngine:
            async def initialize(self):
                raise RuntimeError("MLX unavailable")
            def unload(self):
                pass

        monkeypatch.setattr(dhe, "DeepHermes3Engine", lambda: FailingEngine())

        result = await generate_conceptual_domain_candidates(
            "ransomware threat intelligence leak dark web"
        )
        assert result == [], f"Expected [] on MLX failure, got {result}"


class TestConceptualDomainCandidatesStructure:
    """MLX-generated DomainCandidate has correct structure and bounds."""

    @pytest.mark.asyncio
    async def test_max_conceptual_domains_constant(self):
        """MAX_CONCEPTUAL_DOMAINS is defined and positive."""
        assert MAX_CONCEPTUAL_DOMAINS > 0
        assert isinstance(MAX_CONCEPTUAL_DOMAINS, int)

    @pytest.mark.asyncio
    async def test_mlx_generator_returns_domain_candidate_list(self, monkeypatch):
        """MLX generator returns list of DomainCandidate with correct fields."""
        mock_response = '["ransomware-leak.onion"]'

        class MockEngine:
            async def initialize(self):
                pass

            async def generate(self, prompt, system_msg=None, thinking=False):
                return mock_response

            def unload(self):
                pass

        import hledac.universal.brain.deephermes3_engine as dhe

        monkeypatch.setattr(dhe, "DeepHermes3Engine", lambda: MockEngine())

        from hledac.universal.runtime.nonfeed_candidate_ledger import (
            _generate_conceptual_domains_mlx,
        )

        result = await _generate_conceptual_domains_mlx(
            "ransomware threat intelligence leak dark web"
        )
        assert len(result) == 1
        c = result[0]
        assert isinstance(c, DomainCandidate)
        assert c.domain == "ransomware-leak.onion"
        assert c.confidence == 0.5
        assert c.reason == "mlx_conceptual_generated"
        assert c.source_field == "mlx_conceptual"

    @pytest.mark.asyncio
    async def test_mlx_generator_deduplicates_domains(self, monkeypatch):
        """MLX generator deduplicates returned domains."""
        # DeepHermes3Engine returns duplicate .onion domains in JSON list
        mock_response = '["leak-site.onion", "leak-site.onion", "portal.onion"]'

        class MockEngine:
            async def initialize(self):
                pass

            async def generate(self, prompt, system_msg=None, thinking=False):
                return mock_response

            def unload(self):
                pass

        import hledac.universal.brain.deephermes3_engine as dhe

        monkeypatch.setattr(dhe, "DeepHermes3Engine", lambda: MockEngine())

        from hledac.universal.runtime.nonfeed_candidate_ledger import (
            _generate_conceptual_domains_mlx,
        )

        result = await _generate_conceptual_domains_mlx("ransomware leak dark web")
        domains = [c.domain for c in result]
        assert len(domains) == len(set(domains)), f"Duplicates should be removed: {domains}"

    @pytest.mark.asyncio
    async def test_mlx_generator_respects_max_limit(self, monkeypatch):
        """MLX generator caps results at MAX_CONCEPTUAL_DOMAINS."""
        many_domains = [f"domain{i}.onion" for i in range(20)]
        import json

        class MockEngine:
            async def initialize(self):
                pass

            async def generate(self, prompt, system_msg=None, thinking=False):
                return json.dumps(many_domains)

            def unload(self):
                pass

        import hledac.universal.brain.deephermes3_engine as dhe

        monkeypatch.setattr(dhe, "DeepHermes3Engine", lambda: MockEngine())

        from hledac.universal.runtime.nonfeed_candidate_ledger import (
            _generate_conceptual_domains_mlx,
        )

        result = await _generate_conceptual_domains_mlx("ransomware leak dark web")
        assert len(result) <= MAX_CONCEPTUAL_DOMAINS, (
            f"Expected ≤{MAX_CONCEPTUAL_DOMAINS}, got {len(result)}"
        )

    @pytest.mark.asyncio
    async def test_mlx_generator_validates_onion_domains(self, monkeypatch):
        """._is_valid_domain_candidate rejects non-FQDN but accepts .onion."""
        import json

        mock_response = json.dumps([
            "not-a-valid-domain",  # rejected by _is_valid_domain_candidate
            "breach-forum.onion",  # .onion bypasses _is_valid_domain_candidate check
        ])

        class MockEngine:
            async def initialize(self):
                pass

            async def generate(self, prompt, system_msg=None, thinking=False):
                return mock_response

            def unload(self):
                pass

        import hledac.universal.brain.deephermes3_engine as dhe

        monkeypatch.setattr(dhe, "DeepHermes3Engine", lambda: MockEngine())

        from hledac.universal.runtime.nonfeed_candidate_ledger import (
            _generate_conceptual_domains_mlx,
        )

        result = await _generate_conceptual_domains_mlx("ransomware leak dark web")
        domains = [c.domain for c in result]
        assert "not-a-valid-domain" not in domains, f"Invalid domain should be filtered: {domains}"
        assert "breach-forum.onion" in domains, f".onion should be kept: {domains}"


class TestIntegrationFlow:
    """Full flow: conceptual query → MLX domain generation → CT lane eligibility."""

    @pytest.mark.asyncio
    async def test_ct_lane_eligible_after_mlx_generation(self, monkeypatch):
        """compute_lane_eligibility returns ct=True after MLX generates candidates."""
        import json

        mock_response = json.dumps(["ransomware-portal.com", "breach-forum.io"])

        class MockEngine:
            async def initialize(self):
                pass

            async def generate(self, prompt, system_msg=None, thinking=False):
                return mock_response

            def unload(self):
                pass

        import hledac.universal.brain.deephermes3_engine as dhe

        monkeypatch.setattr(dhe, "DeepHermes3Engine", lambda: MockEngine())

        from hledac.universal.runtime.nonfeed_candidate_ledger import (
            compute_lane_eligibility,
        )

        result = await generate_conceptual_domain_candidates(
            "ransomware threat intelligence leak dark web"
        )
        assert len(result) >= 1, f"Expected ≥1 candidate, got {len(result)}"

        eligibility = compute_lane_eligibility(result)
        assert eligibility["ct"] is True, "CT lane should be eligible with MLX domains"
        assert eligibility["doh"] is True, "DOH lane should be eligible with MLX domains"
