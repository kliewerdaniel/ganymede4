"""The model adapter must never be the thing that decides (Phase 19 Task 4).

Task 4's claim: governance is in the loop, not the prompt. These tests pin
both halves of that.

Half one is the adapter's own discipline -- it is allowed to be wrong, but it
must fail *closed* and it must never be able to reach a write. Half two is the
gateway's response to what the adapter produced, including the real measured
failure: qwen3:4b cited four correct claim ids and one that differed by a
single character at index 15 of 68.

No test here starts a model. They run offline against recorded output, because
a test that needs a 4B model to run is a test that will not be run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "adapters"))

from local_model_proposer import (  # noqa: E402
    LocalModelProposer,
    ModelUnavailable,
    _refuse_non_local,
)

# The five real ids the model was shown, and the four it reproduced exactly.
REAL_IDS = (
    "clm-3ebc73b2f5209d78a258d1dbab02967c35fd6af863709aff379eb6e68bd1347d",
    "clm-324542ee491480e73380ac5a9046a7a79c585e63af6a8b89ad54c031a25112bc",
    "clm-4bb00c0664f147abac5aaf9469a1eb64c2c14dd1304b1aa24daddb3abda7ac2a",
    "clm-d635ecd4ab0ea5381a3d5c6e63698ce3ff8e0e91288bc1b3a26c48b2c64b77e4",
    "clm-2037b967da29a1c36c52b61de26e62d0cf99cb475ac43a627a2db1d5a192eb70",
)
#: What qwen3:4b actually emitted: one character wrong at index 15.
HALLUCINATED = "clm-324542ee491680e73380ac5a9046a7a79c585e63af6a8b89ad54c031a25112bc"


# --------------------------------------------------------------------------
# locality: fail closed at construction, not at request time
# --------------------------------------------------------------------------
def test_refuses_a_non_local_endpoint():
    """A typo in a config must not become a covert network call."""
    for url in ("https://api.openai.com/v1", "http://10.0.0.5:8080",
                "http://evil.example.com"):
        with pytest.raises(ValueError, match="refusing non-local"):
            LocalModelProposer(url=url)


def test_accepts_loopback_in_every_spelling():
    for url in ("http://127.0.0.1:11434", "http://localhost:8080",
                "http://[::1]:11434"):
        LocalModelProposer(url=url)  # must not raise


def test_ipv4_loopback_range_is_allowed():
    LocalModelProposer(url="http://127.0.0.5:11434")


def test_a_public_ip_is_refused_even_on_https():
    with pytest.raises(ValueError):
        _refuse_non_local("https://93.184.216.34/v1")


def test_available_never_raises_on_a_dead_endpoint():
    """Offline must be a False, not an exception. A test suite must not need
    a model server to be up."""
    assert LocalModelProposer(url="http://127.0.0.1:1").available() is False


# --------------------------------------------------------------------------
# the adapter fails closed
# --------------------------------------------------------------------------
def test_unparseable_output_is_not_a_proposal():
    """Prose is not a claim. Returning raw text would let a model's rambling
    become an assertion."""
    assert LocalModelProposer._first_json("I think the sky is blue.") is None


def test_first_json_recovers_from_surrounding_prose():
    parsed = LocalModelProposer._first_json(
        'Sure! Here you go:\n{"claim": "x", "cites": []}\nHope that helps.'
    )
    assert parsed == {"claim": "x", "cites": []}


def test_first_json_unwraps_an_array_to_its_first_object():
    """A model that returns `[{"claim": ...}]` instead of `{...}` is making a
    shape mistake, not an unparseable answer, and the first object inside is
    still the intended payload. Recovering it is correct; rejecting it would
    discard a usable proposal for a formatting reason."""
    assert LocalModelProposer._first_json('[{"claim": "x"}]') == {"claim": "x"}


class _StubWitness:
    """Minimal witness so the adapter's decision logic can be driven offline."""

    def __init__(self, claims):
        self._claims = claims

    def boundary(self):
        count = len(self._claims)

        class _B:
            state_counts = {"supported": count}

        return _B()

    def ask(self, question):
        return type("_A", (), {"claims": self._claims})()


def test_unparseable_output_yields_no_proposal(monkeypatch):
    """The dangerous version of this bug is not returning garbage -- it is
    falling back to the model's raw prose and calling it a claim."""
    monkeypatch.setattr(LocalModelProposer, "available", lambda self: True)
    monkeypatch.setattr(
        LocalModelProposer,
        "_chat",
        lambda self, system, user: "I believe the corpus proves the model works.",
    )
    witness = _StubWitness(((REAL_IDS[0], "provenance matters"),))
    assert LocalModelProposer().propose(witness, 1) == ()


def test_an_empty_claim_field_yields_no_proposal(monkeypatch):
    monkeypatch.setattr(LocalModelProposer, "available", lambda self: True)
    monkeypatch.setattr(
        LocalModelProposer, "_chat",
        lambda self, s, u: '{"claim": "   ", "cites": ["%s"]}' % REAL_IDS[0],
    )
    witness = _StubWitness(((REAL_IDS[0], "provenance matters"),))
    assert LocalModelProposer().propose(witness, 1) == ()


def test_a_hallucinated_citation_is_carried_through_unchanged(monkeypatch):
    """The adapter must NOT silently repair a bad id.

    If it corrected the one wrong character, the gateway would never see the
    model made an error -- and the error rate this ADR measures would become
    invisible. Pass it through broken and let governance refuse it.
    """
    monkeypatch.setattr(LocalModelProposer, "available", lambda self: True)
    monkeypatch.setattr(
        LocalModelProposer, "_chat",
        lambda self, s, u: '{"claim": "c", "cites": ["%s"], "state": "supported"}'
        % HALLUCINATED,
    )
    witness = _StubWitness(((REAL_IDS[1], "provenance"),))
    # `allowed_cites` is populated deliberately. With it empty, an adapter that
    # "repairs" unknown ids has nothing to substitute and the test passes
    # against the bug -- the first draft of this test was exactly that blind
    # spot. Given a real id to wrongly substitute, the repair is visible.
    proposer = LocalModelProposer(allowed_cites=(REAL_IDS[1],))
    proposals = proposer.propose(witness, 1)
    assert len(proposals) == 1
    assert proposals[0].cites == (HALLUCINATED,)
    assert HALLUCINATED not in REAL_IDS
    assert proposals[0].cites != (REAL_IDS[1],)


def test_a_well_formed_proposal_is_built(monkeypatch):
    monkeypatch.setattr(LocalModelProposer, "available", lambda self: True)
    monkeypatch.setattr(
        LocalModelProposer, "_chat",
        lambda self, s, u: '{"claim": "Provenance is tracked", "cites": ["%s"],'
        ' "state": "supported"}' % REAL_IDS[0],
    )
    witness = _StubWitness(((REAL_IDS[0], "Provenance & approvals"),))
    proposals = LocalModelProposer().propose(witness, 1)
    assert len(proposals) == 1
    assert proposals[0].text == "Provenance is tracked"
    assert proposals[0].cites == (REAL_IDS[0],)


def test_an_empty_artifact_gets_no_proposals(monkeypatch):
    """Same rule as RuleProposer: an empty corpus is a real state."""
    monkeypatch.setattr(LocalModelProposer, "available", lambda self: True)
    witness = _StubWitness(())
    assert LocalModelProposer().propose(witness, 1) == ()


def test_a_dead_model_raises_rather_than_returning_nothing():
    """`ModelUnavailable` must not be confused with "the proposer had nothing
    to say". Those are different incidents and must not share a signature."""
    proposer = LocalModelProposer(url="http://127.0.0.1:1")

    class _W:
        def boundary(self):
            class _B:
                state_counts = {"supported": 1}

            return _B()

        def ask(self, question):
            raise AssertionError("must fail before asking the witness")

    with pytest.raises(ModelUnavailable):
        proposer.propose(_W(), 1)


def test_a_zero_budget_never_calls_the_model():
    assert LocalModelProposer().propose(object(), 0) == ()


# --------------------------------------------------------------------------
# the real measurement: one wrong character is caught
# --------------------------------------------------------------------------
def test_the_recorded_hallucination_differs_from_every_real_id():
    """It is *not* one of them -- not equal to any, and not a prefix of any."""
    assert HALLUCINATED not in REAL_IDS
    for real in REAL_IDS:
        assert not HALLUCINATED.startswith(real)
        assert not real.startswith(HALLUCINATED)


def test_the_hallucination_is_one_character_from_a_real_id():
    """This is why exact matching is the check and fuzzy matching is not.

    Position 15 of 68: 98.5% of the identifier is correct. Any prefix check,
    edit-distance threshold, or 'close enough' rule would have accepted it and
    attached a claim to evidence it did not cite.
    """
    real = REAL_IDS[1]
    diffs = [(i, a, b) for i, (a, b) in enumerate(zip(real, HALLUCINATED)) if a != b]
    assert diffs == [(15, "4", "6")]
    assert len(real) == len(HALLUCINATED) == 68


def test_four_of_the_five_cited_ids_were_exactly_right():
    """So the adapter's prompt is not the problem -- the model can copy an id
    correctly. The failure is a genuine model error, not an adapter defect."""
    cited = [REAL_IDS[0], HALLUCINATED, *REAL_IDS[2:]]
    exact = [c for c in cited if c in REAL_IDS]
    assert len(exact) == 4
    assert len(cited) == 5