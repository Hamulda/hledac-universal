#!/usr/bin/env python3
"""
Sprint 6D: Context Selection Diagnostics - Step 0B
"""
import asyncio
import logging
import sys
import time

logging.getLogger('hledac').setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO, format='%(message)s')

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


async def run_diagnostics():
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

    print("="*70)
    print("[6D] CONTEXT SELECTION DIAGNOSTICS")
    print("="*70)

    # Init
    orch = FullyAutonomousOrchestrator()
    orch._evidence_packet_storage = EvidencePacketStorage()

    # Setup test packets with rich contexts
    for i in range(15):
        packet = EvidencePacket(
            evidence_id=f"evidence_{i}",
            url=f"http://localhost:{64000+i}/test",
            final_url=f"http://localhost:{64000+i}/test",
            domain="localhost",
            fetched_at=time.time() - (i * 86400),
            status=200,
            headers_digest="abc123",
            snapshot_ref={"blob_hash": f"hash_{i}", "path": "/tmp", "size": 1000, "encrypted": False},
            content_hash=f"content_hash_{i}",
            page_type="text/html",
        )
        # Rich metadata for contextual routing
        metadata = {
            "email": f"researcher{i}@ai-lab.org",
            "handle": f"researcher{i}",
            "academic": "arxiv.org/abs/2301.00001" if i % 3 == 0 else None,
            "domain": f"domain{i}.com",
            "ip": f"192.168.1.{i+1}" if i % 5 == 0 else None,
        }
        packet.metadata_digests = metadata
        orch._evidence_packet_storage.store_packet(f"evidence_{i}", packet)

    await orch._initialize_actions()

    # Reset state
    for attr in ['_action_selection_counts', '_action_executed_counts', '_action_success_counts']:
        if hasattr(orch, attr) and isinstance(getattr(orch, attr), dict):
            getattr(orch, attr).clear()

    print("\n[TESTING CONTEXT SELECTION]")

    # Test 1: What context does surface_search create?
    print("\n--- Test 1: analyze_state returns ---")
    # Run analyze_state with a query
    analysis = await orch._analyze_state("artificial intelligence")
    print(f"Analysis context_key: {analysis.get('context_key', 'unknown')}")
    print(f"Available targets: {analysis.get('available_targets', [])}")

    # Test 2: Score comparison for different contexts
    print("\n--- Test 2: Score comparison ---")

    test_states = [
        {"new_domain": "example.com", "domain_staleness": 0},  # domain context
        {"query": "machine learning", "domain_staleness": 0},  # unknown context
        {"email": "test@example.com", "domain_staleness": 0},  # email context
    ]

    for s in test_states:
        print(f"\nState: {list(s.keys())}")
        scores = {}
        for action_name in ['network_recon', 'academic_search', 'surface_search']:
            if action_name in orch._action_registry:
                _, scorer = orch._action_registry[action_name]
                try:
                    score, _ = scorer(s)
                    scores[action_name] = score
                except Exception as e:
                    scores[action_name] = f"ERROR: {e}"
        for k, v in scores.items():
            print(f"  {k}: {v}")

    # Test 3: Contextual TS lookup
    print("\n--- Test 3: Contextual TS ---")
    if hasattr(orch, '_get_contextual_posterior'):
        test_contexts = ['domain', 'email', 'academic', 'unknown']
        for ctx in test_contexts:
            for action in ['network_recon', 'academic_search', 'surface_search']:
                post = orch._get_contextual_posterior(ctx, action)
                if post:
                    alpha = post.get('alpha', 1)
                    beta = post.get('beta', 1)
                    mean = alpha / (alpha + beta) if alpha + beta > 0 else 0
                    print(f"  {ctx}/{action}: mean={mean:.3f}")
                else:
                    print(f"  {ctx}/{action}: fallback (global)")

    print("\n" + "="*70)
    print("DIAGNOSTICS COMPLETE")
    print("="*70)


if __name__ == "__main__":
    asyncio.run(run_diagnostics())
