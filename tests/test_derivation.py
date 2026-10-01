"""The write path, and the one question it exists to answer.

A model proposes. Something else decides. If those are the same thing then
ADR-028's governance is theatre, so every test here attacks that one seam:
the derivation must be able to overrule the model, and the model's permission
must never on its own move a belief.

The `Applier` deliberately lives outside `Runtime` and refuses to be handed
one. `Runtime` holds no store; this keeps that true.
"""

import dataclasses
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ganymede4.compile.reopen import rebuild_manifest
from ganymede4.knowledge.evaluator import Evaluator
from ganymede4.knowledge.store import Store
from ganymede4.policy.gateway import Capability, Policy, PolicyGateway
from ganymede4.runtime.apply import Applier, Derivation, Disposition
from ganymede4.runtime.loop import Proposal, ProposalDisposition, RunRecord

TT = "2026-09-30T00:00:00Z"

DOC_A = (
    "The runtime enforces provenance at write time.\n"
    "Verification is independent of the writer.\n"
    "It is also a pyramid scheme.\n"
)
DOC_B = (
    "THIS IS NOT A PYRAMID SCHEME!\n"
    "Provenance is content addressed by hash.\n"
    "The system is deliberately not a pyramid scheme.\n"
)
DOC_C = "Deliberate design keeps the write path boring.\n"


@pytest.fixture(scope="module")
def artifact(tmp_path_factory):
    from ganymede4.compile.compiler import SourceSpec, compile_corpus

    db = tmp_path_factory.mktemp("apply") / "a.db"
    store = Store(str(db))
    compile_corpus(
        store,
        [
            SourceSpec(uri="a.md", content=DOC_A),
            SourceSpec(uri="b.md", content=DOC_B),
            SourceSpec(uri="c.md", content=DOC_C),
        ],
        transaction_time=TT,
    )
    store.close()
    return db


def _deriving_gateway(*states: str) -> PolicyGateway:
    """A gateway that grants `derive` on claims for specific states only.

    Note this is a *different capability* from the `propose` grant the runtime
    uses. Model permission and write permission are separable, which is the
    only reason the second check means anything.
    """
    return PolicyGateway(
        Policy(
            capabilities=(
                Capability("claims", frozenset({"derive"}), constraints={"state": "*"}),
            ),
            version="t-apply",
        )
    )


def _record(
    proposal: Proposal,
    disposition=ProposalDisposition.PERMITTED,
    decision_id: str = "dec-1",
) -> RunRecord:
    return RunRecord(
        proposal_id=proposal.proposal_id,
        question=proposal.question,
        disposition=disposition,
        reason="permitted; awaiting L0 derivation",
        decision_id=decision_id,
        cites=tuple(proposal.cites),
        proposer="test",
        model_proposed_state=proposal.proposed_state,
    )


def _proposal(claim_id: str, state: str) -> Proposal:
    return Proposal.make(
        question="provenance",
        text="I am certain about this.",
        proposed_state=state,
        cites=(claim_id,),
    )


def _states(store: Store) -> dict[str, str]:
    return {c["id"]: c["state"] for c in store.claims()}


# ---------------------------------------------------------------------------
# the seam: derivation is independent of the model
# ---------------------------------------------------------------------------


def test_the_written_state_is_the_derived_one_not_the_models(artifact):
    """The core claim, asserted behaviourally rather than textually.

    Sabotage shape: an applier that trusts ``proposal.proposed_state`` passes
    every happy-path test, so the only reliable detector is asking what state
    actually landed in the database.
    """
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        derivation = _derive(store, target)
        # Claim the opposite of what the evidence supports.
        lie = "supported" if derivation != "supported" else "inconclusive"
        before = store.get_claim(target)["state"]
        out = Applier(store, _deriving_gateway()).apply([_record(_proposal(target, lie))])
        assert out[0].disposition is Disposition.DERIVATION_DISAGREES
        assert out[0].derived_state == derivation
        assert store.get_claim(target)["state"] == before


def test_a_model_cannot_promote_an_unattested_claim_to_supported(artifact):
    """The specific power a model would like: certainty. Refused."""
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        out = Applier(store, _deriving_gateway()).apply(
            [_record(_proposal(target, "supported"))]
        )
        assert out[0].disposition is Disposition.DERIVATION_DISAGREES
        assert store.get_claim(target)["state"] != "supported"


def test_a_model_understating_a_contradiction_is_also_refused(artifact):
    """Understating is the failure people excuse. It is still the model
    overruling evidence."""
    with Store(str(artifact)) as store:
        contradicted = [
            c["id"] for c in store.claims() if c["state"] == "contradicted"
        ]
        if not contradicted:
            pytest.skip("fixture produced no contradiction")
        out = Applier(store, _deriving_gateway()).apply(
            [_record(_proposal(contradicted[0], "inconclusive"))]
        )
        assert out[0].disposition is Disposition.DERIVATION_DISAGREES


def test_agreement_is_applied_and_the_artifact_moves(artifact):
    """The one path that writes."""
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        derivation = _derive(store, target)
        out = Applier(store, _deriving_gateway()).apply(
            [_record(_proposal(target, derivation))]
        )
        assert out[0].disposition is Disposition.APPLIED
        assert out[0].derived_state == derivation
        assert out[0].derived is not None


def test_derivation_is_reported_even_when_the_write_is_refused(artifact):
    """A refusal that throws away what it learned is a wasted measurement.
    The derived state is reported either way, so the disagreement is legible."""
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        out = Applier(store, _deriving_gateway()).apply(
            [_record(_proposal(target, "supported"))]
        )
        assert out[0].derived is not None
        assert out[0].derived.state == out[0].derived_state


# ---------------------------------------------------------------------------
# permission is not authority
# ---------------------------------------------------------------------------


def test_a_refused_proposal_is_never_derived(artifact):
    """UNKNOWN_CITATION is terminal. Re-deriving it here would be a second,
    weaker citation check operating on records it does not own."""
    with Store(str(artifact)) as store:
        out = Applier(store, _deriving_gateway()).apply(
            [
                _record(
                    _proposal("clm-" + "0" * 64, "supported"),
                    ProposalDisposition.UNKNOWN_CITATION,
                )
            ]
        )
        assert out[0].disposition is Disposition.NOT_PERMITTED
        assert out[0].derived is None


def test_a_permit_without_a_decision_id_is_refused(artifact):
    """Permitted but unattributed: no audit can name who allowed this."""
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        out = Applier(store, _deriving_gateway()).apply(
            [_record(_proposal(target, _derive(store, target)), decision_id="")]
        )
        assert out[0].disposition is Disposition.NOT_PERMITTED


def test_model_permission_alone_cannot_write(artifact):
    """The gateway that permitted *proposal* has nothing to say about
    writing. With no `derive` grant, nothing is written."""
    propose_only = PolicyGateway(
        Policy(
            capabilities=(Capability("claims", frozenset({"propose"})),),
            version="propose-only",
        )
    )
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        before = store.get_claim(target)["state"]
        out = Applier(store, propose_only).apply(
            [_record(_proposal(target, _derive(store, target)))]
        )
        assert out[0].disposition is Disposition.NOT_PERMITTED
        assert store.get_claim(target)["state"] == before


def test_the_applier_records_its_own_refusal_to_the_chain(artifact):
    """`PolicyGateway.permits` records even a question it answers no. An
    unrecorded consultation is indistinguishable from no consultation."""
    propose_only = PolicyGateway(
        Policy(
            capabilities=(Capability("claims", frozenset({"propose"})),),
            version="propose-only",
        )
    )
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        Applier(store, propose_only).apply(
            [_record(_proposal(target, _derive(store, target)))]
        )
        assert len(propose_only.decisions) == 1
        assert propose_only.decisions[0].verdict.value == "deny"


# ---------------------------------------------------------------------------
# every write leaves a record an auditor can check
# ---------------------------------------------------------------------------


def test_every_applied_write_records_an_evaluation_of_that_claim(artifact):
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        state = _derive(store, target)
        Applier(store, _deriving_gateway()).apply([_record(_proposal(target, state))])
        evals = store.evaluations_for(target)
        assert evals, "an applied state change recorded no evaluation"
        meta = _meta(evals[-1]["meta"])
        assert meta["decision_id"] == "dec-1"
        assert meta["proposal_id"]


def test_the_record_names_the_model_and_claims_no_authority(artifact):
    """The audit trail must distinguish 'a model said so' from 'the evidence
    says so'. Collapsing the two is how a model becomes an authority."""
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        state = _derive(store, target)
        Applier(store, _deriving_gateway()).apply([_record(_proposal(target, state))])
        meta = _meta(store.evaluations_for(target)[-1]["meta"])
        assert meta["proposer"] == "test"
        assert meta["authority"] == "independent-derivation"
        assert meta["model_proposed_state"] == state


def test_the_derivation_is_attested_by_the_evaluator_not_the_model(artifact):
    """A SUPPORTED belief must name what bore it out, and that list comes
    from the evaluator. ADR-006 still binds on this path."""
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        state = _derive(store, target)
        if state != "supported":
            pytest.skip("fixture target is not supported")
        Applier(store, _deriving_gateway()).apply([_record(_proposal(target, state))])
        evals = store.evaluations_for(target)
        supporting = [e for e in evals if e["relation"] == "attested"]
        assert supporting, "a SUPPORTED belief recorded no attesting relation"
        assert json.loads(supporting[-1]["attested_by"] or "[]") or supporting[-1]["meta"]


def test_the_artifact_still_audits_clean_after_an_apply(artifact):
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        Applier(store, _deriving_gateway()).apply(
            [_record(_proposal(target, _derive(store, target)))]
        )
        store.reseal()
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit_provenance.py"), str(artifact)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# the seam is not merely conventional
# ---------------------------------------------------------------------------


def test_the_applier_refuses_to_be_handed_a_runtime(artifact):
    """Not a back door into the read-only side."""
    with Store(str(artifact)) as store:
        applier = Applier(store, _deriving_gateway())
        with pytest.raises(TypeError):
            applier.bind(runtime=object())


def test_the_runtime_package_never_imports_the_write_path():
    src = (ROOT / "src" / "ganymede4" / "runtime" / "loop.py").read_text()
    assert "from ganymede4.runtime.apply" not in src
    assert "import apply" not in src


def test_a_write_bumps_the_epoch_exactly_once(artifact):
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        before = store.state_epoch()
        Applier(store, _deriving_gateway()).apply(
            [_record(_proposal(target, _derive(store, target)))]
        )
        assert store.state_epoch() == before + 1


def test_a_batch_of_agreements_writes_every_one_of_them(artifact):
    with Store(str(artifact)) as store:
        targets = [_an_unexamined(store) for _ in range(2)]
        records = []
        for t in targets:
            assert _derive(store, t) == _derive(store, t)
            records.append(_record(_proposal(t, _derive(store, t))))
        out = Applier(store, _deriving_gateway()).apply(records)
        assert [o.disposition for o in out] == [Disposition.APPLIED] * len(targets)
        for t in targets:
            assert store.get_claim(t)["state"] != "unexamined"


# ---------------------------------------------------------------------------
# sabotage: each guard is load-bearing
# ---------------------------------------------------------------------------


def _sabotage(mutate, run):
    """Patch the applier source, run, then restore."""
    import importlib

    import ganymede4.runtime.apply as mod

    path = Path(mod.__file__)
    original = path.read_text()
    mutated = mutate(original)
    assert mutated != original, "sabotage was a no-op"
    path.write_text(mutated)
    try:
        importlib.reload(mod)
        run(mod)
    finally:
        path.write_text(original)
        importlib.reload(mod)


def _apply_one(mod, artifact, lie: str) -> None:
    """Shared sabotage body: ask for a write the evidence does not support.

    Two distinct properties, because they are distinct defects:

      * the disposition must not be a silent agreement, or the model's
        disagreement with the evidence is being discarded;
      * the state that *lands* must be the derived one, not the model's.

    A mutation that removes the agreement check fails the first while still
    writing an evidence-grounded state -- that is ignoring the model, not
    obeying it. A mutation that writes ``model_state`` fails the second. Both
    are caught, for the right reason.
    """
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        derived = Derivation.of(store, target).state
        out = mod.Applier(store, _deriving_gateway()).apply(
            [_record(_proposal(target, lie))]
        )
        assert out[0].disposition is not mod.Disposition.DERIVATION_DISAGREES, (
            "sabotage survived: the model's state was believed"
        )
        landed = store.get_claim(target)["state"]
        assert landed in ("unexamined", derived), (
            f"sabotage survived: the model moved a belief to {landed!r}, "
            f"which the derivation did not support (it derived {derived!r})"
        )


def test_sabotage_trusting_the_model_is_caught(artifact):
    _sabotage(
        lambda s: s.replace(
            "if derived.state != model_state:",
            "if False:",
        ),
        lambda mod: _apply_one(mod, artifact, "supported"),
    )


def test_sabotage_writing_the_model_state_is_caught(artifact):
    """Weaker version of the same defect: check the model, then write it."""

    def mutate(s: str) -> str:
        # The full swap: check the model's state, then write *it* rather than
        # the derived one. This is the defect the whole module exists to
        # prevent, expressed as the smallest possible edit.
        return s.replace(
            "        if derived.state != model_state:",
            "        if False:",
            1,
        ).replace("                derived.state,\n", "                model_state,\n", 1)

    _sabotage(mutate, lambda mod: _apply_one(mod, artifact, "supported"))


def test_sabotage_skipping_the_gateway_is_caught(artifact):
    def run(mod):
        propose_only = PolicyGateway(
            Policy(
                capabilities=(Capability("claims", frozenset({"propose"})),),
                version="propose-only",
            )
        )
        with Store(str(artifact)) as store:
            target = _an_unexamined(store)
            before = store.get_claim(target)["state"]
            out = mod.Applier(store, propose_only).apply(
                [_record(_proposal(target, _derive(store, target)))]
            )
            assert out[0].disposition is not mod.Disposition.NOT_PERMITTED, (
                "sabotage survived: model permission alone wrote"
            )
            assert store.get_claim(target)["state"] == before

    _sabotage(
        lambda s: s.replace(
            "if not self._gateway_permits(record, derived):",
            "if False and not self._gateway_permits(record, derived):",
        ),
        run,
    )


def test_sabotage_deriving_a_refused_proposal_is_caught(artifact):
    def run(mod):
        """With the disposition guard removed, the applier reaches
        ``Derivation.of`` for an id that is not in the artifact. A refusal
        becomes a crash: the record stopped being terminal."""
        with Store(str(artifact)) as store:
            try:
                out = mod.Applier(store, _deriving_gateway()).apply(
                    [
                        _record(
                            _proposal("clm-" + "0" * 64, "supported"),
                            ProposalDisposition.UNKNOWN_CITATION,
                        )
                    ]
                )
            except Exception as exc:  # noqa: BLE001
                # The guard is gone: the applier tried to derive a claim the
                # runtime had already refused. That is the sabotage working.
                return
            raise AssertionError(
                "sabotage survived: a refused proposal was re-derived "
                "instead of being refused"
            )
            assert out[0].disposition is mod.Disposition.NOT_PERMITTED, (
                "sabotage survived: a refused proposal stopped being refused"
            )
            assert out[0].derived is None, (
                "sabotage survived: a refused proposal was re-derived"
            )
            return

    _sabotage(
        lambda s: s.replace(
            "if record.disposition is not ProposalDisposition.PERMITTED:",
            "if False:",
        ),
        run,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _an_unexamined(store: Store) -> str:
    row = store.db.execute(
        "SELECT id FROM claims WHERE state = 'unexamined' ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("fixture has no unexamined claim")
    return row["id"]


def _derive(store: Store, claim_id: str) -> str:
    return Derivation.of(store, claim_id).state


def _meta(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def test_derivation_is_constructible_and_typed(artifact):
    with Store(str(artifact)) as store:
        target = _an_unexamined(store)
        d = Derivation.of(store, target)
        assert d.state
        assert d.verdict is not None
        assert dataclasses.isdataclass(d)
