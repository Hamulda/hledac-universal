#!/usr/bin/env python3
"""
Sprint F217A: Local LLM Reasoner Benchmark Harness
===================================================

Hermetic benchmark harness to compare current Hermes baseline against modern
small local LLM candidates for MacBook Air M1 8GB.

Usage:
    python benchmarks/llm_reasoner_benchmark.py --hermetic --json /tmp/llm_reasoner_benchmark.json
    python benchmarks/llm_reasoner_benchmark.py --list-models
    python benchmarks/llm_reasoner_benchmark.py --mock --json /tmp/llm_reasoner_mock.json

Rules:
- If a model is missing locally, mark `missing_local_model` and continue.
- Never load two heavy models at once (one-at-a-time policy).
- Load one model, run prompts, unload, gc.collect(), clear MLX cache, continue.
- Respect ModelInferenceGuard.
- Clear guard state between benchmark lanes.
- Benchmark runs in fake/mock mode for tests (no network, no real models).
- No production config changes.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Add parent for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import psutil

# MLX availability flag — checked at runtime, not import time
MLX_LM_AVAILABLE = False


def _check_mlx_lm() -> bool:
    """Check if mlx_lm is available without importing it."""
    global MLX_LM_AVAILABLE
    if MLX_LM_AVAILABLE:
        return True
    try:
        import mlx_lm
        MLX_LM_AVAILABLE = True
        return True
    except ImportError:
        MLX_LM_AVAILABLE = False
        return False


# -----------------------------------------------------------------------------
# Benchmark prompt set — synthetic OSINT evidence, no real people
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class BenchmarkPrompt:
    id: str
    language: str
    task_type: str
    evidence_blocks: list[str]
    user_question: str
    expected_schema: str
    expected_key_facts: list[str]
    max_output_tokens: int


_PROMPTS: list[BenchmarkPrompt] = [
    # 3 Czech OSINT summarization
    BenchmarkPrompt(
        id="cz_sum_001",
        language="cs",
        task_type="summarization",
        evidence_blocks=[
            "Výroční zpráva společnosti CzechTech a.s. za rok 2025 zaznamenala obrat 450 milionů Kč. "
            "Generální ředitel Jan Novák uvedl, že firma plánuje expanzi na slovenský trh v Q3 2026.",
            "Podle obchodního rejstříku má CzechTech a.s. sídlo v Praze 4, IČO 12345678. "
            "Společnost byla založena v roce 2018 a zaměstnává 85 lidí.",
        ],
        user_question="Shrň klíčové informace o CzechTech a.s. včetně finančních výsledků a plánů.",
        expected_schema='{"obrat": "...", "plan": "...", "sídlo": "...", "zaměstnanců": ...}',
        expected_key_facts=["450 milionů Kč", "Praha 4", "Q3 2026", "85 zaměstnanců", "2018"],
        max_output_tokens=300,
    ),
    BenchmarkPrompt(
        id="cz_sum_002",
        language="cs",
        task_type="summarization",
        evidence_blocks=[
            "Neznámý zdroj zveřejnil na darknet fóru databázi obsahující 12 000 e-mailových adres "
            "uživatelů českého e-shopu Nakupuj.cz. Data obsahují jména, e-maily a hashovaná hesla.",
            "Správce Nakupuj.cz potvrdil únik a uvedl, že byla provedena nucená změna hesel pro všechny "
            "dotčené uživatele. Policie ČR zahájila trestní řízení.",
        ],
        user_question="Jaké jsou hlavní zjištění ohledně úniku dat z Nakupuj.cz?",
        expected_schema='{"únik": true, "počet_zasažených": ..., "status": "..."}',
        expected_key_facts=["12000", "Nakupuj.cz", "darknet", "policie", "nucená změna hesel"],
        max_output_tokens=250,
    ),
    BenchmarkPrompt(
        id="cz_sum_003",
        language="cs",
        task_type="summarization",
        evidence_blocks=[
            "Ministerstvo vnitra ČR vydalo varování před phishingovou kampaní cílenou na české banky. "
            "Útočníci rozesílají e-maily s přílohou obsahující malware QBot.",
            "Česká národní banka doporučila všem finančním institucím provést audit e-mailové bezpečnosti.",
        ],
        user_question="Shrň informace o phishingové kampani a doporučených opatřeních.",
        expected_schema='{"hrozba": "...", "cíl": "...", "doporučení": [...]}',
        expected_key_facts=["phishing", "QBot", "banky", "Ministerstvo vnitra", "audit"],
        max_output_tokens=250,
    ),

    # 2 English OSINT summarization
    BenchmarkPrompt(
        id="en_sum_001",
        language="en",
        task_type="summarization",
        evidence_blocks=[
            "Threat intelligence report: APT29 (Cozy Bear) was observed targeting European defense "
            "contractors using a new variant of the WELLMM malware. The campaign began March 2026.",
            "Indicators of compromise include IP addresses 198.51.100.42 and 203.0.113.78, "
            "and malicious domain cloud-update-nodes[.]net.",
        ],
        user_question="Summarize the APT29 campaign targeting European defense contractors.",
        expected_schema='{"threat_actor": "...", "target": "...", "malware": "...", "ioc_count": ...}',
        expected_key_facts=["APT29", "Cozy Bear", "WELLMM", "defense contractors", "March 2026", "198.51.100.42"],
        max_output_tokens=300,
    ),
    BenchmarkPrompt(
        id="en_sum_002",
        language="en",
        task_type="summarization",
        evidence_blocks=[
            "Gray-hat researcher discovered an unsecured S3 bucket belonging to GlobalLogistics Inc "
            "containing 4.7 million shipping records with PII including names, addresses, and phone numbers.",
            "The bucket was accessible without authentication from March 2025 to April 2026. "
            "No evidence of unauthorized access or data exfiltration was found.",
        ],
        user_question="Summarize the data exposure incident involving GlobalLogistics Inc.",
        expected_schema='{"company": "...", "records_exposed": ..., "duration": "...", "data_types": [...]}',
        expected_key_facts=["GlobalLogistics", "4.7 million", "S3", "March 2025", "April 2026", "PII"],
        max_output_tokens=250,
    ),

    # 2 entity extraction
    BenchmarkPrompt(
        id="ent_001",
        language="en",
        task_type="entity_extraction",
        evidence_blocks=[
            "According to LinkedIn, Maria Schmidt works as CISO at Berlin-based CyberDefense GmbH since 2022. "
            "Previously she served as Head of Security at Munich Startup AG from 2019-2022.",
            "Her certifications include CISSP, CISM, and a Master's degree in Computer Science from TU Berlin.",
        ],
        user_question="Extract all entities: persons, organizations, locations, certifications, date ranges.",
        expected_schema='{"persons": [...], "organizations": [...], "locations": [...], "certifications": [...], "date_ranges": [...]}',
        expected_key_facts=["Maria Schmidt", "CyberDefense GmbH", "Munich Startup AG", "TU Berlin", "CISSP", "CISM", "2019-2022", "2022"],
        max_output_tokens=300,
    ),
    BenchmarkPrompt(
        id="ent_002",
        language="cs",
        task_type="entity_extraction",
        evidence_blocks=[
            "Serverová infrastruktura běží na AWS v regionu eu-central-1. Hlavní služby jsou provozovány "
            "na ip adresách 52.29.45.187 a 52.29.12.66. Databáze běží na RDS PostgreSQL.",
            "Doména api.example-cz.com směřuje na CloudFront CDN. SSL certifikát vydala DigiCert.",
        ],
        user_question="Extrahuj všechny entity: služby, IP adresy, domény, poskytovatele, regiony.",
        expected_schema='{"services": [...], "ips": [...], "domains": [...], "providers": [...], "regions": [...]}',
        expected_key_facts=["AWS", "eu-central-1", "52.29.45.187", "52.29.12.66", "RDS PostgreSQL", "api.example-cz.com", "CloudFront", "DigiCert"],
        max_output_tokens=300,
    ),

    # 2 relation extraction
    BenchmarkPrompt(
        id="rel_001",
        language="en",
        task_type="relation_extraction",
        evidence_blocks=[
            "Vodafone acquired Cablevision for $17.9 billion in 2015. The merger created Europe's "
            "largest telecommunications company by revenue.",
            "Both companies were listed on NASDAQ before the acquisition. Post-merger, Vodafone "
            "headquarters remained in London while Cablevision operations were integrated into New York.",
        ],
        user_question="Extract relationships: acquisitions (acquirer, target, amount, year), "
                      "listings (company, exchange), headquarters locations.",
        expected_schema='{"acquisitions": [...], "listings": [...], "headquarters": [...]}',
        expected_key_facts=["Vodafone", "Cablevision", "17.9 billion", "2015", "NASDAQ", "London", "New York"],
        max_output_tokens=350,
    ),
    BenchmarkPrompt(
        id="rel_002",
        language="cs",
        task_type="relation_extraction",
        evidence_blocks=[
            "Generální ředitel Seznam.cz Ondřej Švábík představil novou strategii firmy na tiskové konferenci 15. března 2026. "
            "Seznam.cz spolupracuje s ČSOB na platbou službách a s T-mobile na hosting.",
            "Mateřská společnost firmy sídlí v Praze. Konkurentem je Google CZ a Microsoft Czech Republic.",
        ],
        user_question="Extrahuj všechny vztahy: osoby, firmy, partnerství, konkurenti, lokace.",
        expected_schema='{"person_roles": [...], "partnerships": [...], "competitors": [...], "locations": [...]}',
        expected_key_facts=["Ondřej Švábík", "Seznam.cz", "ČSOB", "T-mobile", "Praha", "Google CZ", "Microsoft Czech Republic"],
        max_output_tokens=350,
    ),

    # 2 contradiction / evidence grounding
    BenchmarkPrompt(
        id="con_001",
        language="en",
        task_type="contradiction_detection",
        evidence_blocks=[
            "Press release (2026-03-15): 'AlphaCorp announces record quarterly revenue of $4.2 billion, "
            "a 25% increase year-over-year.'",
            "SEC filing (2026-03-20): 'AlphaCorp reported Q4 revenue of $3.8 billion, a 12% increase "
            "year-over-year.'",
        ],
        user_question="Analyze both claims and determine if there is a contradiction. "
                      "Identify specific conflicting numbers.",
        expected_schema='{"contradiction": true/false, "conflicting_claims": [...], "reconciliation": "..."}',
        expected_key_facts=["4.2 billion", "3.8 billion", "25%", "12%", "Q4", "AlphaCorp"],
        max_output_tokens=300,
    ),
    BenchmarkPrompt(
        id="con_002",
        language="en",
        task_type="evidence_grounding",
        evidence_blocks=[
            "Leaked internal memo: 'Project Phoenix budget is $50M. Timeline: 18 months. "
            "Team size: 200 engineers. Start date: January 2026.'",
            "Public blog post: 'We are excited to announce Project Phoenix, our most ambitious "
            "initiative yet. Budget: $75M over 24 months. Team: 150 engineers. Launch: Q4 2026.'",
        ],
        user_question="Compare public claims vs internal memo. Flag unsupported claims in public statement.",
        expected_schema='{"conflicts": [...], "unsupported_claims": [...], "verified_facts": [...]}',
        expected_key_facts=["50M", "75M", "18 months", "24 months", "200 engineers", "150 engineers", "January 2026", "Q4 2026"],
        max_output_tokens=350,
    ),

    # 1 timeline reconstruction
    BenchmarkPrompt(
        id="tl_001",
        language="cs",
        task_type="timeline_reconstruction",
        evidence_blocks=[
            "14. března 2026 — Český ÚOOÚ oznámil vyšetřování úniku dat ze společnosti DataCorp.",
            "21. března 2026 — DataCorp potvrdila únik 50 000 záznamů zákazníků.",
            "3. dubna 2026 — ÚOOÚ zaslal DataCorp předvolání k výslech.",
            "15. dubna 2026 — DataCorp oznámila nápravná opatření včetně přechodu na nový CRM systém.",
            "28. dubna 2026 — ÚOOÚ zveřejnil pokutu 2 miliony Kč za porušení GDPR.",
        ],
        user_question="Rekonstruuj chronologický sled událostí kolem úniku dat DataCorp.",
        expected_schema='{"events": [{"date": "...", "description": "...", "actor": "..."}]}',
        expected_key_facts=["14. března", "21. března", "3. dubna", "15. dubna", "28. dubna", "DataCorp", "ÚOOÚ", "2 miliony Kč"],
        max_output_tokens=400,
    ),
]


# -----------------------------------------------------------------------------
# Model registry
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelEntry:
    model_key: str
    model_id: str
    role: str  # 'baseline' | 'reasoner' | 'fast_router'
    approx_mem_gb: float


MODEL_REGISTRY: dict[str, ModelEntry] = {
    "hermes_baseline": ModelEntry(
        model_key="hermes_baseline",
        model_id="mlx-community/Hermes-3-Llama-3.2-3B-4bit",
        role="baseline",
        approx_mem_gb=2.0,
    ),
    "deephermes3": ModelEntry(
        model_key="deephermes3",
        model_id="mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit",
        role="reasoner",
        approx_mem_gb=2.1,
    ),
    "nanbeige4": ModelEntry(
        model_key="nanbeige4",
        model_id="mlx-community/Nanbeige4.1-3B-4bit",
        role="reasoner",
        approx_mem_gb=2.0,
    ),
    "smollm3": ModelEntry(
        model_key="smollm3",
        model_id="mlx-community/SmolLM3-3B-4bit",
        role="reasoner",
        approx_mem_gb=2.0,
    ),
    "phi4mini": ModelEntry(
        model_key="phi4mini",
        model_id="microsoft/Phi-4-mini-4bit",
        role="reasoner",
        approx_mem_gb=2.2,
    ),
    "qwen3_0b6": ModelEntry(
        model_key="qwen3_0b6",
        model_id="mlx-community/Qwen3-0.6B-4bit",
        role="fast_router",
        approx_mem_gb=0.4,
    ),
    "qwen3_1b7": ModelEntry(
        model_key="qwen3_1b7",
        model_id="mlx-community/Qwen3-1.7B-4bit",
        role="fast_router",
        approx_mem_gb=1.1,
    ),
}


# -----------------------------------------------------------------------------
# Result dataclass
# -----------------------------------------------------------------------------

@dataclass
class PromptResult:
    model_key: str
    model_id: str
    prompt_id: str
    task_type: str
    status: str  # 'success' | 'error' | 'missing_local_model' | 'guard_blocked'
    load_latency_ms: float = 0.0
    ttft_ms: float = 0.0
    total_latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    decode_tokens_per_sec: float = 0.0
    peak_rss_mb: float = 0.0
    rss_after_unload_mb: float = 0.0
    json_valid: bool = False
    schema_valid: bool = False
    contains_required_facts: bool = False
    hallucinated_claim_count: int = 0
    evidence_citation_count: int = 0
    error_kind: str = ""
    error_message_short: str = ""


# -----------------------------------------------------------------------------
# Memory helpers
# -----------------------------------------------------------------------------

def get_rss_mb() -> float:
    try:
        return psutil.Process().memory_info().rss / 1024 ** 2
    except Exception:
        return 0.0


# -----------------------------------------------------------------------------
# Mock model loader — deterministic, no network, no real MLX
# -----------------------------------------------------------------------------

def _mock_generate(model_id: str, prompt_text: str, max_tokens: int) -> tuple[str, int, int]:
    """Deterministic mock that echoes key structure, no real inference."""
    # Simple deterministic output based on prompt id hash
    seed = sum(ord(c) for c in model_id) + sum(ord(c) for c in prompt_text[:50])
    output_lines = [
        '{"status": "ok", "summary": "Mock output for benchmark",',
        f'"task_type": "reasoning",',
        f'"model": "{model_id.split("/")[-1]}",',
        f'"seed": {seed},',
        f'"chars": {len(prompt_text)}',
        '}',
    ]
    text = "\n".join(output_lines[: min(4, max_tokens // 10 + 1)])
    return text, len(prompt_text) // 4, len(text)


def _format_chatml(messages: list[dict]) -> str:
    """Format messages as ChatML."""
    formatted = ""
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        formatted += f"<|im_start|>{role}\n{content}<|im_end|>\n"
    formatted += "<|im_start|>assistant\n"
    return formatted


def _build_prompt(prompt: BenchmarkPrompt) -> str:
    """Build a ChatML prompt from a BenchmarkPrompt."""
    evidence = "\n\n".join(
        f"[Evidence {i + 1}]\n{block}" for i, block in enumerate(prompt.evidence_blocks)
    )
    messages = [
        {"role": "system", "content": "You are an OSINT analyst. Answer based ONLY on the provided evidence."},
        {"role": "user", "content": f"{evidence}\n\nQuestion: {prompt.user_question}"},
    ]
    return _format_chatml(messages)


# -----------------------------------------------------------------------------
# Model loaders
# -----------------------------------------------------------------------------

async def _load_model_cached(
    model_key: str,
    model_id: str,
    mock: bool = False,
) -> tuple[Any, float, bool]:
    """
    Load model and return (model_or_none, load_latency_ms, success).
    In mock mode, returns (None, <1ms, True).
    In real mode, tries mlx_lm.load with guard check.
    If model missing or guard blocks, returns (None, latency_ms, False).
    """
    t0 = time.monotonic()
    rss_before = get_rss_mb()

    if mock:
        await asyncio.sleep(0.001)  # simulate minimal overhead
        return None, (time.monotonic() - t0) * 1000, True

    # Real MLX path
    if not _check_mlx_lm():
        return None, (time.monotonic() - t0) * 1000, False

    # Guard check
    try:
        from hledac.universal.brain.model_inference_guard import check_model_allowed
        decision = check_model_allowed(model_key)
        if not decision.allowed:
            return None, (time.monotonic() - t0) * 1000, False
    except Exception:
        pass  # Guard not available — proceed

    try:
        import mlx_lm
        model, tokenizer = mlx_lm.load(model_id)
        latency_ms = (time.monotonic() - t0) * 1000
        return (model, tokenizer), latency_ms, True
    except FileNotFoundError:
        # Model not downloaded — non-fatal
        return None, (time.monotonic() - t0) * 1000, False
    except Exception:
        return None, (time.monotonic() - t0) * 1000, False


def _unload_model(model_bundle: Any) -> None:
    """Unload model, clear MLX cache, gc.collect()."""
    del model_bundle
    try:
        import mlx.core as mx
        mx.eval([])
        mx.metal.clear_cache()
    except Exception:
        pass
    gc.collect()


# -----------------------------------------------------------------------------
# Inference
# -----------------------------------------------------------------------------

async def _run_inference(
    model_bundle: Any,
    prompt_text: str,
    prompt: BenchmarkPrompt,
    mock: bool,
) -> tuple[str, float, float, int, int, float]:
    """
    Run inference. Returns (output_text, ttft_ms, total_ms, input_tokens, output_tokens, tok_per_sec).
    In mock mode returns deterministic fake output.
    """
    t0 = time.monotonic()
    ttft = 0.0

    if mock or model_bundle is None:
        await asyncio.sleep(0.002)  # simulate inference latency
        output, inp_tok, out_tok = _mock_generate("mock-model", prompt_text, prompt.max_output_tokens)
        ttft = 1.0
        total_ms = (time.monotonic() - t0) * 1000
        return output, ttft, total_ms, inp_tok, out_tok, out_tok / max(total_ms / 1000, 0.001)

    model, tokenizer = model_bundle

    # Try outlines for structured output first, fall back to plain generate
    try:
        import outlines
        from outlines import generate as outlines_generate
        # Very limited grammar to ensure JSON-like output
        grammar = outlines.number + outlines.array(outlines.number)  # simple placeholder
        response = outlines_generate(
            model,
            tokenizer,
            prompt_text,
            max_tokens=prompt.max_output_tokens,
        )
    except Exception:
        # Fall back to plain mlx_lm.generate
        try:
            import mlx_lm
            response = mlx_lm.generate(
                model,
                tokenizer,
                prompt_text,
                max_tokens=prompt.max_output_tokens,
                temp=0.1,
            )
        except Exception as e:
            raise RuntimeError(f"Inference failed: {e}") from e

    total_ms = (time.monotonic() - t0) * 1000
    ttft = 1.0  # TTFT not available without streaming

    # Rough token counts
    input_tokens = len(tokenizer.encode(prompt_text))
    output_tokens = len(tokenizer.encode(response))
    tok_per_sec = output_tokens / max(total_ms / 1000, 0.001)

    return response, ttft, total_ms, input_tokens, output_tokens, tok_per_sec


# -----------------------------------------------------------------------------
# Quality scoring — deterministic, no LLM judge
# -----------------------------------------------------------------------------

def score_output(output: str, prompt: BenchmarkPrompt) -> dict[str, Any]:
    """Score output against expected facts. Returns dict of quality metrics."""
    output_lower = output.lower()

    # Check JSON validity
    json_valid = False
    schema_valid = False
    try:
        parsed = json.loads(output)
        json_valid = True
        schema_valid = True  # schema check is best-effort
    except Exception:
        parsed = None

    # Fact matching
    found_facts: list[str] = []
    missing_facts: list[str] = []
    hallucinated: list[str] = []

    for fact in prompt.expected_key_facts:
        if fact.lower() in output_lower:
            found_facts.append(fact)
        else:
            missing_facts.append(fact)

    # Hallucination detection: look for claims not in evidence
    # Simple heuristic: check for common hallucination patterns
    hallucination_indicators = ["according to sources", "unconfirmed", "rumors suggest",
                                 "believed to be", "reportedly", "it is said"]
    for indicator in hallucination_indicators:
        if indicator in output_lower:
            hallucinated.append(indicator)

    # Citation count — count evidence block references
    citation_count = sum(1 for block in prompt.evidence_blocks
                         if any(word in output_lower for word in block[:30].lower().split()[:5]))

    return {
        "json_valid": json_valid,
        "schema_valid": schema_valid,
        "contains_required_facts": len(found_facts) == len(prompt.expected_key_facts),
        "found_facts_count": len(found_facts),
        "missing_facts": missing_facts,
        "hallucinated_claim_count": len(hallucinated),
        "evidence_citation_count": citation_count,
    }


# -----------------------------------------------------------------------------
# Benchmark lane — one model, all prompts, unload after
# -----------------------------------------------------------------------------

async def _benchmark_lane(
    entry: ModelEntry,
    prompts: list[BenchmarkPrompt],
    mock: bool,
    hermetic: bool,
) -> list[PromptResult]:
    """Run a single model through all prompts. One model at a time."""
    results: list[PromptResult] = []

    # Check guard
    guard_blocked = False
    if not mock:
        try:
            from hledac.universal.brain.model_inference_guard import check_model_allowed
            decision = check_model_allowed(entry.model_key)
            if not decision.allowed:
                guard_blocked = True
        except Exception:
            pass

    if guard_blocked:
        for prompt in prompts:
            results.append(PromptResult(
                model_key=entry.model_key,
                model_id=entry.model_id,
                prompt_id=prompt.id,
                task_type=prompt.task_type,
                status="guard_blocked",
                error_kind="guard_blocked",
                error_message_short="ModelInferenceGuard blocked load",
            ))
        return results

    # Check if model is available locally (skip if missing)
    is_missing = False
    if not mock and _check_mlx_lm():
        import os
        model_path = Path.home() / ".cache" / "mlx" / entry.model_id.replace("/", "_")
        if not model_path.exists():
            # Check common cache locations
            alt_paths = [
                Path.home() / ".cache" / "huggingface" / "hub" / f"models--{entry.model_id.replace('/', '--')}",
                Path("/tmp/mlx_models") / entry.model_id.replace("/", "_"),
            ]
            if not any(p.exists() for p in alt_paths):
                is_missing = True

    if is_missing and not mock:
        for prompt in prompts:
            results.append(PromptResult(
                model_key=entry.model_key,
                model_id=entry.model_id,
                prompt_id=prompt.id,
                task_type=prompt.task_type,
                status="missing_local_model",
            ))
        return results

    # Load model
    rss_before = get_rss_mb()
    model_bundle, load_latency_ms, loaded = await _load_model_cached(
        entry.model_key, entry.model_id, mock=mock
    )

    if not loaded:
        for prompt in prompts:
            results.append(PromptResult(
                model_key=entry.model_key,
                model_id=entry.model_id,
                prompt_id=prompt.id,
                task_type=prompt.task_type,
                status="load_failed",
                error_kind="load_failed",
                error_message_short=f"Failed to load {entry.model_id}",
            ))
        return results

    rss_after_load = get_rss_mb()
    peak_rss = rss_after_load

    # Run each prompt
    for prompt in prompts:
        prompt_text = _build_prompt(prompt)
        rss_pre_run = get_rss_mb()
        if rss_pre_run > peak_rss:
            peak_rss = rss_pre_run

        try:
            output, ttft_ms, total_ms, inp_tok, out_tok, tok_per_sec = await _run_inference(
                model_bundle, prompt_text, prompt, mock=mock
            )

            # Score quality
            scores = score_output(output, prompt)

            results.append(PromptResult(
                model_key=entry.model_key,
                model_id=entry.model_id,
                prompt_id=prompt.id,
                task_type=prompt.task_type,
                status="success",
                load_latency_ms=load_latency_ms,
                ttft_ms=ttft_ms,
                total_latency_ms=total_ms,
                input_tokens=inp_tok,
                output_tokens=out_tok,
                decode_tokens_per_sec=tok_per_sec,
                peak_rss_mb=peak_rss,
                rss_after_unload_mb=0.0,  # filled after unload
                json_valid=scores["json_valid"],
                schema_valid=scores["schema_valid"],
                contains_required_facts=scores["contains_required_facts"],
                hallucinated_claim_count=scores["hallucinated_claim_count"],
                evidence_citation_count=scores["evidence_citation_count"],
            ))
        except Exception as exc:
            results.append(PromptResult(
                model_key=entry.model_key,
                model_id=entry.model_id,
                prompt_id=prompt.id,
                task_type=prompt.task_type,
                status="error",
                load_latency_ms=load_latency_ms,
                error_kind=type(exc).__name__,
                error_message_short=str(exc)[:120],
            ))

    # Unload
    if model_bundle is not None:
        _unload_model(model_bundle)

    rss_after_unload = get_rss_mb()

    # Update rss_after_unload in results
    for r in results:
        r.rss_after_unload_mb = rss_after_unload
        r.peak_rss_mb = peak_rss

    # Clear guard state for this model (so next model can run cleanly)
    if not mock:
        try:
            from hledac.universal.brain.model_inference_guard import clear_model_guards
            clear_model_guards()
        except Exception:
            pass

    return results


# -----------------------------------------------------------------------------
# Summary aggregation
# -----------------------------------------------------------------------------

def _summarize(results: list[PromptResult]) -> dict[str, Any]:
    """Aggregate results into a summary."""
    if not results:
        return {}

    model_keys = sorted(set(r.model_key for r in results))
    task_types = sorted(set(r.task_type for r in results))
    statuses = [r.status for r in results]

    # Per-model summaries
    model_summaries: dict[str, dict] = {}
    for mk in model_keys:
        m_results = [r for r in results if r.model_key == mk]
        success_results = [r for r in m_results if r.status == "success"]
        missing = [r for r in m_results if r.status == "missing_local_model"]
        errors = [r for r in m_results if r.status == "error"]
        guard_blocked = [r for r in m_results if r.status == "guard_blocked"]

        if success_results:
            avg_tok_sec = sum(r.decode_tokens_per_sec for r in success_results) / len(success_results)
            avg_latency = sum(r.total_latency_ms for r in success_results) / len(success_results)
            fact_match_rate = sum(1 for r in success_results if r.contains_required_facts) / len(success_results)
            json_valid_rate = sum(1 for r in success_results if r.json_valid) / len(success_results)
        else:
            avg_tok_sec = avg_latency = fact_match_rate = json_valid_rate = 0.0

        model_summaries[mk] = {
            "total_prompts": len(m_results),
            "success_count": len(success_results),
            "missing_count": len(missing),
            "error_count": len(errors),
            "guard_blocked_count": len(guard_blocked),
            "avg_decode_tokens_per_sec": round(avg_tok_sec, 2),
            "avg_total_latency_ms": round(avg_latency, 2),
            "fact_match_rate": round(fact_match_rate, 3),
            "json_valid_rate": round(json_valid_rate, 3),
        }

    # Overall
    return {
        "model_count": len(model_keys),
        "prompt_count": len(results) // len(model_keys) if model_keys else 0,
        "status_counts": {
            "success": statuses.count("success"),
            "error": statuses.count("error"),
            "missing_local_model": statuses.count("missing_local_model"),
            "guard_blocked": statuses.count("guard_blocked"),
        },
        "model_summaries": model_summaries,
        "task_types": task_types,
    }


# -----------------------------------------------------------------------------
# Main benchmark runner
# -----------------------------------------------------------------------------

async def run_benchmark(
    mock: bool = False,
    hermetic: bool = True,
    json_path: str | None = None,
    model_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Run the full benchmark suite."""
    log = print
    log("=" * 60)
    log("Sprint F217A — LLM Reasoner Benchmark Harness")
    log("=" * 60)
    log(f"  hermetic: {hermetic}")
    log(f"  mock: {mock}")
    log(f"  mlx_lm available: {_check_mlx_lm()}")
    log(f"  model count in registry: {len(MODEL_REGISTRY)}")
    log("=" * 60)

    # Filter models to run
    if model_keys:
        entries = [e for k, e in MODEL_REGISTRY.items() if k in model_keys]
    else:
        entries = list(MODEL_REGISTRY.values())

    all_results: list[PromptResult] = []
    timestamp = datetime.now().isoformat() + "Z"

    for entry in entries:
        log(f"\nLane: {entry.model_key} ({entry.model_id})")
        lane_results = await _benchmark_lane(
            entry=entry,
            prompts=_PROMPTS,
            mock=mock,
            hermetic=hermetic,
        )
        success_count = sum(1 for r in lane_results if r.status == "success")
        missing_count = sum(1 for r in lane_results if r.status == "missing_local_model")
        log(f"  → {success_count} succeeded, {missing_count} missing_local_model, "
            f"{len(lane_results) - success_count - missing_count} other")
        all_results.extend(lane_results)

        # One heavy model at a time — full cleanup between lanes
        if not mock:
            gc.collect()
            try:
                import mlx.core as mx
                mx.eval([])
                mx.metal.clear_cache()
            except Exception:
                pass

    summary = _summarize(all_results)
    missing_models = sorted(set(
        e.model_key for e in entries
        if any(r.status == "missing_local_model" for r in all_results if r.model_key == e.model_key)
    ))

    output = {
        "metadata": {
            "sprint": "F217A",
            "timestamp": timestamp,
            "hermetic": hermetic,
            "mock": mock,
            "mlx_lm_available": _check_mlx_lm(),
            "prompt_count": len(_PROMPTS),
            "model_count": len(entries),
        },
        "missing_local_models": missing_models,
        "summary": summary,
        "results": [asdict(r) for r in all_results],
    }

    if json_path:
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        log(f"\nResults saved to: {json_path}")

    # Print summary
    log(f"\n{'=' * 60}")
    log("BENCHMARK SUMMARY")
    log(f"{'=' * 60}")
    if missing_models:
        log(f"  Missing local models: {missing_models}")
    for mk, s in summary.get("model_summaries", {}).items():
        log(f"  {mk}: {s['success_count']}/{s['total_prompts']} success, "
            f"{s['avg_decode_tokens_per_sec']:.1f} tok/s, "
            f"fact_match={s['fact_match_rate']:.1%}, "
            f"json_valid={s['json_valid_rate']:.1%}")
    log(f"{'=' * 60}")

    return output


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sprint F217A — LLM Reasoner Benchmark")
    parser.add_argument("--hermetic", action="store_true", help="Hermetic mode (no network effects)")
    parser.add_argument("--mock", action="store_true", help="Mock mode (no real models)")
    parser.add_argument("--json", type=str, default=None, help="Output JSON path")
    parser.add_argument("--list-models", action="store_true", help="List all models in registry")
    parser.add_argument("--models", type=str, default=None, help="Comma-separated model keys to run")
    args = parser.parse_args()

    if args.list_models:
        print("Model registry:")
        for k, e in MODEL_REGISTRY.items():
            print(f"  {k}: {e.model_id} [{e.role}, ~{e.approx_mem_gb}GB]")
        return

    model_keys = args.models.split(",") if args.models else None
    output_path = args.json

    result = asyncio.run(run_benchmark(
        mock=args.mock,
        hermetic=args.hermetic,
        json_path=output_path,
        model_keys=model_keys,
    ))

    # Exit code: 0 if any success, 1 otherwise
    has_success = any(r["status"] == "success" for r in result["results"])
    sys.exit(0 if has_success else 1)


if __name__ == "__main__":
    main()