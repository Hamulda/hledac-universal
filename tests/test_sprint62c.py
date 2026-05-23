def test_beta_binomial_basic():
    from hledac.universal.brain.confidence_utils import BetaBinomial
    bb = BetaBinomial()
    bb.add_support(1.0)
    bb.add_contradict(0.5)
    m = bb.mean()
    assert 0.0 <= m <= 1.0
    assert bb.conflict() >= 0.0
