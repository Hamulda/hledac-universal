"""
Characterization tests for ConsensusProposal behavior methods.
No behavior change — these tests document current behavior.
"""
import pytest

from coordinators.swarm_coordinator import ConsensusProposal


class TestConsensusProposalGetResult:
    """ConsensusProposal.get_result — pure unit, no deps."""

    def test_get_result_no_votes_returns_false_zero(self):
        prop = ConsensusProposal(proposal_id="p1", proposal_type="test", data={})
        accepted, confidence = prop.get_result()
        assert accepted is False
        assert confidence == 0.0

    def test_get_result_all_yes_returns_true(self):
        prop = ConsensusProposal(proposal_id="p1", proposal_type="test", data={})
        prop.add_vote("n1", True, weight=1.0)
        prop.add_vote("n2", True, weight=1.0)
        prop.add_vote("n3", True, weight=1.0)
        accepted, confidence = prop.get_result()
        assert accepted is True
        assert confidence == pytest.approx(0.6)  # 3 votes / 5 = 0.6

    def test_get_result_all_no_returns_false(self):
        prop = ConsensusProposal(proposal_id="p1", proposal_type="test", data={})
        prop.add_vote("n1", False, weight=1.0)
        prop.add_vote("n2", False, weight=1.0)
        accepted, confidence = prop.get_result()
        assert accepted is False
        assert confidence == pytest.approx(0.4)  # 2 votes / 5 = 0.4

    def test_get_result_majority_yes_accepted(self):
        """3 yes / 2 no = 60% yes_weight > 50% → accepted."""
        prop = ConsensusProposal(proposal_id="p1", proposal_type="test", data={})
        prop.add_vote("n1", True, weight=1.0)
        prop.add_vote("n2", True, weight=1.0)
        prop.add_vote("n3", True, weight=1.0)
        prop.add_vote("n4", False, weight=1.0)
        prop.add_vote("n5", False, weight=1.0)
        accepted, _ = prop.get_result()
        assert accepted is True

    def test_get_result_majority_no_rejected(self):
        """2 yes / 3 no = 40% yes_weight < 50% → rejected."""
        prop = ConsensusProposal(proposal_id="p1", proposal_type="test", data={})
        prop.add_vote("n1", True, weight=1.0)
        prop.add_vote("n2", True, weight=1.0)
        prop.add_vote("n3", False, weight=1.0)
        prop.add_vote("n4", False, weight=1.0)
        prop.add_vote("n5", False, weight=1.0)
        accepted, _ = prop.get_result()
        assert accepted is False

    def test_get_result_weighted_votes_determine_outcome(self):
        """High-rep node (weight 3) voting no tilts the vote."""
        prop = ConsensusProposal(proposal_id="p1", proposal_type="test", data={})
        prop.add_vote("n1", True, weight=1.0)   # yes: 1
        prop.add_vote("high_rep", False, weight=3.0)  # no: 3
        # total = 4, yes = 1, ratio = 0.25 → rejected
        accepted, _ = prop.get_result()
        assert accepted is False

    def test_get_result_equal_weights_exactly_50_50(self):
        """Exactly 50% yes = 0.5 acceptance_rate > 0.5 → accepted (barely)."""
        prop = ConsensusProposal(proposal_id="p1", proposal_type="test", data={})
        prop.add_vote("n1", True, weight=1.0)
        prop.add_vote("n2", False, weight=1.0)
        # total_weight = 2, yes_weight = 1, rate = 0.5 → 0.5 > 0.5 is False
        accepted, _ = prop.get_result()
        assert accepted is False

    def test_get_result_confidence_grows_with_vote_count(self):
        """Confidence = min(1.0, len(votes) / 5.0)."""
        prop = ConsensusProposal(proposal_id="p1", proposal_type="test", data={})
        for i in range(5):
            prop.add_vote(f"n{i}", True, weight=1.0)
        _, confidence = prop.get_result()
        assert confidence == 1.0  # 5/5 = 1.0

        prop2 = ConsensusProposal(proposal_id="p2", proposal_type="test", data={})
        for i in range(3):
            prop2.add_vote(f"n{i}", True, weight=1.0)
        _, confidence2 = prop2.get_result()
        assert confidence2 == pytest.approx(0.6)  # 3/5 = 0.6


class TestConsensusProposalAddVote:
    """ConsensusProposal.add_vote — records vote and weight."""

    def test_add_vote_stores_vote(self):
        prop = ConsensusProposal(proposal_id="p1", proposal_type="test", data={})
        prop.add_vote("n1", True, weight=2.0)
        assert prop.votes["n1"] is True
        assert prop.vote_weights["n1"] == 2.0

    def test_add_vote_overwrites_previous(self):
        prop = ConsensusProposal(proposal_id="p1", proposal_type="test", data={})
        prop.add_vote("n1", True, weight=1.0)
        prop.add_vote("n1", False, weight=3.0)
        assert prop.votes["n1"] is False
        assert prop.vote_weights["n1"] == 3.0
