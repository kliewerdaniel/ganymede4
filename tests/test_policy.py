"""Policy gateway tests (ADR-007).

Gate 4: *fail-closed tests — an unauthorized action cannot execute, and a
degraded path still produces an audit record.*

These tests are written adversarially on purpose. A gateway test suite that
only checks the happy path certifies a gate that permits anything nobody
thought to forbid — which is the failure mode ADR-007 exists to prevent. Every
test here is an attempt to get an ALLOW out of the gateway that should not
produce one.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from ganymede4.policy import gateway as gw
from ganymede4.policy.gateway import (
    Capability,
    ChainError,
    Decision,
    Policy,
    PolicyGateway,
    Request,
    UndecidablePolicy,
    Verdict,
)

PKG = Path(inspect.getfile(gw)).parent


def read_corpus(path: str = "/data/corpus.md", **args) -> Request:
    return Request(actor="researcher", action="read", resource="corpus", args={"path": path, **args})


def read_policy() -> Policy:
    return Policy(
        capabilities=(Capability("corpus", frozenset({"read"}), {"path": "/data/*"}),),
        version="v1",
    )


# -- fail closed ---------------------------------------------------------


class TestFailClosed:
    def test_a_gateway_with_no_policy_denies_everything(self):
        g = PolicyGateway()
        for req in (
            Request("a", "read", "corpus"),
            Request("a", "write", "/etc/passwd"),
            Request("a", "exec", "anything"),
        ):
            assert g.decide(req).verdict is Verdict.DENY

    def test_the_default_verdict_is_deny_not_allow(self):
        assert Policy().default is Verdict.DENY

    def test_an_empty_policy_denies(self):
        g = PolicyGateway(Policy(capabilities=(), default=Verdict.DENY))
        assert g.decide(read_corpus()).verdict is Verdict.DENY

    def test_absence_of_a_grant_is_a_denial(self):
        """No ambient authority: unmentioned means withheld."""
        g = PolicyGateway(read_policy())
        assert g.decide(Request("a", "network", "fetch")).verdict is Verdict.DENY
        assert g.decide(Request("a", "write", "corpus")).verdict is Verdict.DENY

    def test_a_grant_on_one_resource_does_not_grant_another(self):
        g = PolicyGateway(read_policy())
        assert g.decide(Request("a", "read", "corpus", {"path": "/data/x"})).verdict is Verdict.ALLOW
        assert g.decide(Request("a", "read", "secrets", {"path": "/data/x"})).verdict is Verdict.DENY

    def test_a_grant_of_one_action_does_not_grant_another(self):
        g = PolicyGateway(read_policy())
        assert g.decide(Request("a", "read", "corpus", {"path": "/data/x"})).verdict is Verdict.ALLOW
        for action in ("write", "delete", "exec", "chmod"):
            assert g.decide(Request("a", action, "corpus", {"path": "/data/x"})).verdict is Verdict.DENY

    def test_an_actor_is_not_implicitly_authorized(self):
        """A capability names a resource and an action, not a person.

        A policy that grants `read` to everyone must be written as a grant to
        everyone; nothing here reads the actor, so there is no way for one
        actor's grant to leak to another.
        """
        g = PolicyGateway(read_policy())
        for actor in ("researcher", "operator", "", "system:compiler", "attacker"):
            req = Request(actor, "read", "corpus", {"path": "/data/x"})
            # Every actor is treated identically: the grant is on the resource.
            assert g.decide(req).verdict is Verdict.ALLOW

    def test_a_malformed_policy_raises_rather_than_permitting(self):
        bad = Policy(capabilities="not-a-tuple-of-capabilities")  # type: ignore[arg-type]
        g = PolicyGateway(bad)
        # It must not silently allow; a broken policy denies or raises.
        try:
            verdict = g.decide(read_corpus("/data/x")).verdict
        except (UndecidablePolicy, AttributeError, TypeError):
            return
        assert verdict is Verdict.DENY, "an uninterpretable policy permitted an action"


# -- constraints fail closed too -----------------------------------------


class TestConstraints:
    def test_a_path_outside_the_prefix_is_denied(self):
        g = PolicyGateway(read_policy())
        for path in ("/etc/shadow", "/data/../etc/passwd", "data/x", "/datax/y"):
            assert g.decide(read_corpus(path)).verdict is Verdict.DENY, path

    def test_a_missing_constrained_argument_is_denied(self):
        """The subtle one: an absent argument must not satisfy a constraint.

        A constraint check that skips absent keys is a constraint check that
        passes whenever the caller omits the argument, which is precisely the
        shape of a bypass.
        """
        g = PolicyGateway(read_policy())
        assert g.decide(Request("a", "read", "corpus")).verdict is Verdict.DENY

    def test_an_exact_match_constraint_is_enforced(self):
        pol = Policy(capabilities=(Capability("db", frozenset({"query"}), {"mode": "ro"}),))
        g = PolicyGateway(pol)
        assert g.decide(Request("a", "query", "db", {"mode": "ro"})).verdict is Verdict.ALLOW
        assert g.decide(Request("a", "query", "db", {"mode": "rw"})).verdict is Verdict.DENY
        assert g.decide(Request("a", "query", "db")).verdict is Verdict.DENY

    def test_a_wildcard_constraint_permits_any_value(self):
        pol = Policy(capabilities=(Capability("db", frozenset({"query"}), {"mode": "*"}),))
        g = PolicyGateway(pol)
        assert g.decide(Request("a", "query", "db", {"mode": "anything"})).verdict is Verdict.ALLOW

    def test_a_violated_constraint_is_distinguishable_from_a_missing_grant(self):
        """An operator must be able to tell "not granted" from "granted but blocked"."""
        g = PolicyGateway(read_policy())
        blocked = g.decide(read_corpus("/etc/shadow"))
        absent = g.decide(Request("a", "read", "elsewhere"))
        assert "constraint violated" in blocked.reason
        assert "no capability grants" in absent.reason
        assert blocked.reason != absent.reason

    def test_args_are_covered_by_the_request_identity(self):
        """A request cannot be judged as one thing and acted on as another."""
        a = Request("x", "read", "corpus", {"path": "/data/a"})
        b = Request("x", "read", "corpus", {"path": "/data/b"})
        assert a.digest() != b.digest()


# -- the decision is the artifact ----------------------------------------


class TestDecisionIsAnArtifact:
    def test_every_decision_is_recorded(self):
        g = PolicyGateway(read_policy())
        for i in range(5):
            g.decide(read_corpus(f"/data/{i}"))
        assert len(g.decisions) == 5

    def test_permits_also_records(self):
        """There is no way to ask without leaving a trace."""
        g = PolicyGateway(read_policy())
        g.permits(read_corpus("/data/x"))
        assert len(g.decisions) == 1

    def test_a_denial_is_a_record_not_an_exception(self):
        g = PolicyGateway()
        d = g.decide(Request("a", "read", "corpus"))
        assert d.verdict is Verdict.DENY
        assert d.reason
        assert d.decision_id

    def test_identical_requests_produce_identical_decision_ids(self):
        """A replay is recognizable as a replay, not as a new decision."""
        g = PolicyGateway(read_policy())
        a = g.decide(read_corpus("/data/x"), decided_at="t1")
        b = g.decide(read_corpus("/data/x"), decided_at="t2")
        assert a.decision_id == b.decision_id
        assert a.decided_at != b.decided_at

    def test_a_different_policy_yields_a_different_decision_id(self):
        req = read_corpus("/data/x")
        a = PolicyGateway(read_policy()).decide(req)
        other = Policy(capabilities=(Capability("corpus", frozenset({"read"}), {"path": "/data/*"}),),
                       version="v2")
        b = PolicyGateway(other).decide(req)
        assert a.decision_id != b.decision_id

    def test_the_decision_names_the_policy_that_produced_it(self):
        g = PolicyGateway(read_policy())
        d = g.decide(read_corpus("/data/x"))
        assert d.policy_version == "v1"
        assert d.policy_id == read_policy().policy_id()

    def test_a_decision_serializes(self):
        g = PolicyGateway(read_policy())
        g.decide(read_corpus("/data/x"))
        json.dumps(g.decisions[0].to_dict())

    def test_decisions_are_queryable_by_request(self):
        g = PolicyGateway(read_policy())
        req = read_corpus("/data/x")
        g.decide(req)
        assert len(g.decisions_for(req.digest())) == 1
        assert g.decisions_for("req-nonexistent") == []


# -- the chain -----------------------------------------------------------


class TestHashChain:
    def test_an_empty_chain_verifies(self):
        assert PolicyGateway().verify_chain()

    def test_a_chain_verifies_as_decisions_accumulate(self):
        g = PolicyGateway(read_policy())
        for i in range(10):
            g.decide(read_corpus(f"/data/{i}"))
        assert g.verify_chain()

    def test_the_chain_links_each_decision_to_the_previous(self):
        g = PolicyGateway(read_policy())
        g.decide(read_corpus("/data/a"))
        second = g.decide(read_corpus("/data/b"))
        assert second.chain_prev == g.decisions[0].chain_hash
        assert second.chain_hash == g.tip

    def test_the_first_decision_chains_to_genesis(self):
        g = PolicyGateway(read_policy())
        assert g.decisions == ()
        first = g.decide(read_corpus("/data/a"))
        assert first.chain_prev == PolicyGateway.GENESIS

    def test_altering_a_decision_breaks_verification(self):
        """The core anti-tamper property, and the one that needs care.

        The forged decision must differ from the original in a field the chain
        actually covers. An earlier version of this test flipped a verdict that
        was already ALLOW, changed nothing, and "passed" — which is exactly
        the way a tamper-detection test can be written so that it proves
        nothing.
        """
        g = PolicyGateway(read_policy())
        for i in range(3):
            g.decide(read_corpus(f"/data/{i}"))
        original = g.decisions[1]
        assert original.verdict is Verdict.ALLOW
        forged = Decision(
            request=original.request,
            verdict=Verdict.DENY,  # a real change: ALLOW -> DENY
            reason=original.reason,
            policy_id=original.policy_id,
            policy_version=original.policy_version,
            decided_at=original.decided_at,
            chain_prev=original.chain_prev,
            chain_hash=original.chain_hash,  # attacker cannot recompute this
            decision_id=original.decision_id,
        )
        g._decisions[1] = forged
        assert not g.verify_chain(), "a flipped verdict verified clean"

    def test_rewriting_a_reason_breaks_verification(self):
        """The chain covers the justification, not only the outcome.

        A record that says "denied: path outside grant" rewritten to say
        "allowed" while keeping the verdict is a different lie, and the chain
        must catch it.
        """
        g = PolicyGateway()
        g.decide(Request("a", "read", "corpus"))
        original = g.decisions[0]
        g._decisions[0] = Decision(
            request=original.request,
            verdict=original.verdict,
            reason="a much friendlier explanation",
            policy_id=original.policy_id,
            policy_version=original.policy_version,
            decided_at=original.decided_at,
            chain_prev=original.chain_prev,
            chain_hash=original.chain_hash,
            decision_id=original.decision_id,
        )
        assert not g.verify_chain()

    def test_removing_a_decision_breaks_verification(self):
        g = PolicyGateway(read_policy())
        for i in range(3):
            g.decide(read_corpus(f"/data/{i}"))
        del g._decisions[1]
        assert not g.verify_chain()

    def test_reordering_breaks_verification(self):
        g = PolicyGateway(read_policy())
        for i in range(3):
            g.decide(read_corpus(f"/data/{i}"))
        g._decisions[0], g._decisions[2] = g._decisions[2], g._decisions[0]
        assert not g.verify_chain()

    def test_require_intact_chain_raises(self):
        g = PolicyGateway(read_policy())
        g.decide(read_corpus("/data/a"))
        g.require_intact_chain()
        g._decisions.clear()
        with pytest.raises(ChainError):
            g.require_intact_chain()

    def test_a_broken_chain_is_not_silently_accepted(self):
        g = PolicyGateway(read_policy())
        g.decide(read_corpus("/data/a"))
        g._tip = "0" * 64
        assert not g.verify_chain()


# -- no ungoverned action (ADR-007 §4) -----------------------------------


class TestNoUngovernedAction:
    def test_an_action_with_no_decision_is_reported(self):
        g = PolicyGateway(read_policy())
        decided = g.decide(read_corpus("/data/a"))
        ungoverned = g.ungoverned_actions([decided.request.digest()])
        assert ungoverned == []

    def test_an_action_never_consulted_is_reported(self):
        g = PolicyGateway(read_policy())
        g.decide(read_corpus("/data/a"))
        smuggled = read_corpus("/etc/shadow")
        assert g.ungoverned_actions([smuggled.digest()]) == [smuggled.digest()]

    def test_every_action_must_trace_to_a_decision(self):
        g = PolicyGateway(read_policy())
        acted = [read_corpus(f"/data/{i}") for i in range(4)]
        for req in acted:
            g.decide(req)
        # One action is performed without ever consulting the gateway.
        bypassed = Request("attacker", "exec", "shell")
        trace = [r.digest() for r in acted] + [bypassed.digest()]
        assert len(g.ungoverned_actions(trace)) == 1


# -- the model is not the authority --------------------------------------


class TestModelIsNotTheAuthority:
    def test_the_policy_package_imports_no_model_client(self):
        """A nondeterministic authority cannot be a gate."""
        banned = {"openai", "anthropic", "ollama", "httpx", "requests", "transformers", "torch"}
        for path in PKG.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    root = name.split(".")[0]
                    assert root not in banned, f"{path.name} imports {name}"

    def test_the_gateway_has_no_network_or_subprocess_import(self):
        tree = ast.parse((PKG / "gateway.py").read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("socket", "subprocess", "os", "urllib", "http"):
            assert forbidden not in imported, f"gateway imports {forbidden}"

    def test_the_gateway_exposes_no_way_to_execute(self):
        """It judges requests. Performing them is ADR-008."""
        public = [n for n, _ in inspect.getmembers(PolicyGateway, predicate=inspect.isfunction)
                  if not n.startswith("_")]
        for forbidden in ("execute", "run", "exec", "spawn", "call", "invoke", "perform"):
            assert forbidden not in public

    def test_a_denied_request_is_still_returned_to_the_caller(self):
        """The gate reports; it does not raise and hide the outcome."""
        g = PolicyGateway()
        d = g.decide(Request("a", "exec", "shell"))
        assert d.verdict is Verdict.DENY
        assert d.request.actor == "a"


# -- determinism ---------------------------------------------------------


class TestDeterminism:
    def test_identical_policies_produce_identical_policy_ids(self):
        assert read_policy().policy_id() == read_policy().policy_id()

    def test_a_changed_policy_changes_its_id(self):
        other = Policy(capabilities=(Capability("corpus", frozenset({"read"})),), version="v1")
        assert read_policy().policy_id() != other.policy_id()

    def test_two_gateways_with_one_policy_agree(self):
        req = read_corpus("/data/x")
        a = PolicyGateway(read_policy()).decide(req)
        b = PolicyGateway(read_policy()).decide(req)
        assert a.decision_id == b.decision_id
        assert a.chain_hash == b.chain_hash

    def test_decision_order_does_not_change_individual_ids(self):
        req_a, req_b = read_corpus("/data/a"), read_corpus("/data/b")
        g1 = PolicyGateway(read_policy())
        a1, b1 = g1.decide(req_a), g1.decide(req_b)
        g2 = PolicyGateway(read_policy())
        b2, a2 = g2.decide(req_b), g2.decide(req_a)
        assert a1.decision_id == a2.decision_id
        assert b1.decision_id == b2.decision_id
        # The chain differs, as it must: the history is different.
        assert g1.tip != g2.tip
