"""
F214: Feed Dominance Guard — smoke tests.
Validates the three cases from the task spec:
  case A: feed=5058, public=2, total=5060 -> recommend_nonfeed_diagnostic True
  case B: feed=50, public=50  -> recommend_nonfeed_diagnostic False
  case C: feed=100, nonfeed=0, no eligible lanes -> True but no hard block by default
"""
import sys

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')

from runtime.sprint_scheduler import FeedDominanceGuard


def test_case_a():
    """Case A: feed=5058, public=2, total=5060 -> recommend_nonfeed_diagnostic True."""
    g = FeedDominanceGuard(strict=False)
    result = g.compute(
        total_accepted=5060,
        feed_accepted=5058,
        nonfeed_accepted=2,
    )
    assert result.should_recommend_nonfeed_diagnostic is True, (
        f"A: expected True, got {result.should_recommend_nonfeed_diagnostic}"
    )
    assert result.feed_dominance_ratio == 5058 / 5060, f"A ratio wrong: {result.feed_dominance_ratio}"
    assert result.feed_dominance_class == "feed_only_like", f"A class: {result.feed_dominance_class}"
    print("PASS case A")


def test_case_b():
    """Case B: feed=50, public=50 -> recommend_nonfeed_diagnostic False."""
    g = FeedDominanceGuard(strict=False)
    result = g.compute(
        total_accepted=100,
        feed_accepted=50,
        nonfeed_accepted=50,
    )
    assert result.should_recommend_nonfeed_diagnostic is False, (
        f"B: expected False, got {result.should_recommend_nonfeed_diagnostic}"
    )
    assert result.feed_dominance_class == "balanced", f"B class: {result.feed_dominance_class}"
    print("PASS case B")


def test_case_c():
    """Case C: feed=100, nonfeed=0, no eligible lanes -> True but no hard block by default."""
    g = FeedDominanceGuard(strict=False)  # default strict=False
    result = g.compute(
        total_accepted=100,
        feed_accepted=100,
        nonfeed_accepted=0,
        eligible_nonfeed_lanes_terminal=False,
        nonfeed_diagnostic_timed_out=False,
    )
    assert result.should_recommend_nonfeed_diagnostic is True, (
        f"C: expected True, got {result.should_recommend_nonfeed_diagnostic}"
    )
    assert result.guard_triggered is True, f"C guard_triggered: {result.guard_triggered}"
    assert result.block_early_exit is False, "C block_early_exit should be False (strict=False)"
    print("PASS case C (default mode)")


def test_case_c_strict():
    """Case C strict: feed=100, nonfeed=0, no eligible lanes -> hard block by default."""
    g = FeedDominanceGuard(strict=True)
    result = g.compute(
        total_accepted=100,
        feed_accepted=100,
        nonfeed_accepted=0,
        eligible_nonfeed_lanes_terminal=False,
        nonfeed_diagnostic_timed_out=False,
    )
    assert result.should_recommend_nonfeed_diagnostic is True
    assert result.guard_triggered is True
    assert result.block_early_exit is True, f"C strict: expected block=True, got {result.block_early_exit}"
    print("PASS case C (strict mode — block)")


def test_case_c_strict_allowed():
    """Case C strict with nonfeed>=5: no block."""
    g = FeedDominanceGuard(strict=True, min_nonfeed_findings=5)
    result = g.compute(
        total_accepted=105,
        feed_accepted=100,
        nonfeed_accepted=5,
    )
    assert result.block_early_exit is False, "C strict min_nonfeed: expected block=False"
    print("PASS case C (strict, nonfeed>=5 — allow)")


def test_case_c_strict_terminal():
    """Case C strict with all lanes terminal: no block."""
    g = FeedDominanceGuard(strict=True)
    result = g.compute(
        total_accepted=100,
        feed_accepted=100,
        nonfeed_accepted=0,
        eligible_nonfeed_lanes_terminal=True,
        nonfeed_diagnostic_timed_out=False,
    )
    assert result.block_early_exit is False, "C strict terminal: expected block=False"
    print("PASS case C (strict, lanes terminal — allow)")


def test_case_c_strict_timedout():
    """Case C strict with diagnostic timed out: no block."""
    g = FeedDominanceGuard(strict=True)
    result = g.compute(
        total_accepted=100,
        feed_accepted=100,
        nonfeed_accepted=0,
        eligible_nonfeed_lanes_terminal=False,
        nonfeed_diagnostic_timed_out=True,
    )
    assert result.block_early_exit is False, "C strict timedout: expected block=False"
    print("PASS case C (strict, diagnostic timed out — allow)")


def test_case_balanced():
    """80/20 split -> balanced, no guard."""
    g = FeedDominanceGuard()
    result = g.compute(
        total_accepted=100,
        feed_accepted=80,
        nonfeed_accepted=20,
    )
    assert result.feed_dominance_class == "balanced"
    assert result.guard_triggered is False
    assert result.should_recommend_nonfeed_diagnostic is False
    print("PASS balanced")


def test_case_no_findings():
    """Zero findings -> balanced."""
    g = FeedDominanceGuard()
    result = g.compute(
        total_accepted=0,
        feed_accepted=0,
        nonfeed_accepted=0,
    )
    assert result.feed_dominance_class == "balanced"
    assert result.should_recommend_nonfeed_diagnostic is False
    print("PASS no findings")


if __name__ == "__main__":
    test_case_a()
    test_case_b()
    test_case_c()
    test_case_c_strict()
    test_case_c_strict_allowed()
    test_case_c_strict_terminal()
    test_case_c_strict_timedout()
    test_case_balanced()
    test_case_no_findings()
    print("\nAll F214 smoke tests PASSED")
