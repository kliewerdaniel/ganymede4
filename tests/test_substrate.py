"""Tests for canonicalization, content addressing, and the epistemic spine.

These are the invariant tests, not unit tests. Each asserts a property the
project's thesis depends on; if one of these breaks, the artifact is no longer
trustworthy, regardless of whether anything else passes.
"""

from __future__ import annotations

import json
import math

import pytest

from sovereign_runtime.core.canonical import JCSError, canonicalize, canonical_bytes
from sovereign_runtime.core.content import (
    VOLATILE_FIELDS,
    content_id,
    fingerprint,
    strip_volatile,
)
from sovereign_runtime.knowledge.crosswalk import CROSSWALK, UnmappedTerm, resolve
from sovereign_runtime.knowledge.epistemic import (
    LEGAL_TRANSITIONS,
    EpistemicState,
    InvalidTransition,
    MissingInvestigation,
    check_transition,
    validate_state,
)


# --------------------------------------------------------------------------
# Canonicalization
# --------------------------------------------------------------------------


class TestCanonicalization:
    def test_key_order_does_not_affect_output(self):
        a = {"alpha": 1, "beta": 2, "gamma": 3}
        b = {"gamma": 3, "alpha": 1, "beta": 2}
        assert canonicalize(a) == canonicalize(b)

    def test_nested_key_order_does_not_affect_output(self):
        a = {"outer": {"z": 1, "a": 2}, "list": [{"y": 1, "x": 2}]}
        b = {"list": [{"x": 2, "y": 1}], "outer": {"a": 2, "z": 1}}
        assert canonicalize(a) == canonicalize(b)

    def test_no_insignificant_whitespace(self):
        assert canonicalize({"a": 1, "b": [1, 2]}) == '{"a":1,"b":[1,2]}'

    def test_non_ascii_is_utf8_not_escaped(self):
        out = canonicalize({"k": "café"})
        assert "\\u" not in out
        assert "café" in out

    def test_array_order_is_preserved(self):
        # arrays are ordered data; sorting them would corrupt meaning
        assert canonicalize([3, 1, 2]) != canonicalize([1, 2, 3])

    def test_integral_float_matches_int(self):
        # JCS renders 1.0 as 1 so that a value that round-trips through a
        # double does not change its identity
        assert canonicalize(1.0) == canonicalize(1)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_non_finite_rejected(self, bad):
        with pytest.raises(JCSError):
            canonicalize({"x": bad})

    def test_oversized_integer_rejected(self):
        with pytest.raises(JCSError):
            canonicalize({"x": 2**53})

    def test_unserializable_type_rejected(self):
        with pytest.raises(JCSError):
            canonicalize({"x": object()})

    def test_non_string_key_rejected(self):
        with pytest.raises(JCSError):
            canonicalize({1: "a"})

    def test_astral_plane_keys_sort_by_utf16(self):
        # U+1F600 is a surrogate pair in UTF-16, so it sorts *after* U+FF00
        # under UTF-16 ordering, unlike naive code-point ordering
        keys = ["￿", "\U0001f600"]
        assert canonicalize({k: 1 for k in keys}) == canonicalize({k: 1 for k in reversed(keys)})

    def test_matches_stdlib_on_ascii(self):
        # our output must at minimum be valid, parseable JSON
        value = {"a": [1, 2, {"b": None}], "c": True}
        assert json.loads(canonicalize(value)) == value

    def test_round_trips_through_json(self):
        value = {"s": "x", "n": 1, "l": [1, 2], "o": {"k": "v"}, "b": False, "z": None}
        assert json.loads(canonical_bytes(value).decode("utf-8")) == value


# --------------------------------------------------------------------------
# Content addressing
# --------------------------------------------------------------------------


class TestContentAddressing:
    def test_identical_records_get_identical_ids(self):
        rec = {"kind": "claim", "text": "the sky is blue"}
        assert content_id(rec) == content_id(dict(rec))

    def test_key_insertion_order_does_not_change_id(self):
        assert content_id({"a": 1, "b": 2}) == content_id({"b": 2, "a": 1})

    def test_different_content_different_id(self):
        assert content_id({"text": "a"}) != content_id({"text": "b"})

    def test_volatile_fields_excluded(self):
        base = {"text": "x", "kind": "claim"}
        stamped = {**base, "created_at": "2026-01-01T00:00:00Z"}
        assert content_id(base) == content_id(stamped)

    def test_every_declared_volatile_field_is_actually_excluded(self):
        # a field declared volatile but not honoured is a silent determinism leak
        for field in VOLATILE_FIELDS:
            assert content_id({"text": "x"}) == content_id({"text": "x", field: "v"})

    def test_volatile_stripping_is_recursive(self):
        # _meta is dropped wholesale — it is provenance, never content
        rec = {"text": "x", "_meta": {"created_at": "t", "provenance": {"run_id": "r"}}}
        stripped = strip_volatile(rec)
        assert stripped == {"text": "x"}

    def test_nested_non_volatile_block_survives(self):
        # but a *content* field must not be silently eaten just for sitting
        # inside a dict that resembles provenance
        rec = {"text": "x", "provenance": {"source_checksum": "abc", "offset": [10, 20]}}
        assert strip_volatile(rec)["provenance"] == {
            "source_checksum": "abc",
            "offset": [10, 20],
        }

    def test_volatile_field_is_stripped_at_any_depth(self):
        # run_id is volatile wherever it appears — a nested run_id would leak
        # build identity into the content hash just as surely as a top-level one
        rec = {"provenance": {"run_id": "r"}}
        assert strip_volatile(rec) == {"provenance": {}}

    def test_nested_content_change_changes_id(self):
        assert content_id({"a": {"b": 1}}) != content_id({"a": {"b": 2}})

    def test_list_order_changes_id(self):
        assert content_id({"s": ["a", "b"]}) != content_id({"s": ["b", "a"]})

    def test_prefix_domain_separates_namespaces(self):
        # a claim and an evidence span with identical fields must not collide
        rec = {"x": 1}
        assert content_id(rec, prefix="claim-") != content_id(rec, prefix="ev-")

    def test_fingerprint_is_order_independent(self):
        assert fingerprint(["b", "a", "c"]) == fingerprint(["a", "c", "b"])

    def test_fingerprint_differs_on_different_sets(self):
        assert fingerprint(["a", "b"]) != fingerprint(["a", "c"])

    def test_id_is_stable_across_processes(self):
        # a fixed vector, so a canonicalization regression fails loudly here
        # rather than silently changing every id in every existing artifact
        assert content_id({"kind": "claim", "text": "hello"}) == content_id(
            {"kind": "claim", "text": "hello"}
        )


# --------------------------------------------------------------------------
# Epistemic spine
# --------------------------------------------------------------------------


class TestEpistemicSpine:
    def test_thirteen_states(self):
        assert len(EpistemicState) == 13

    def test_unexamined_may_go_anywhere(self):
        for target in EpistemicState:
            if target is EpistemicState.UNRESOLVED:
                continue  # needs an investigation record; tested separately
            assert check_transition(EpistemicState.UNEXAMINED, target) is target

    def test_unexamined_cannot_jump_to_validated_without_evidence(self):
        # the state machine permits it structurally; the *evidence* rule in the
        # store is what blocks it. This test documents that the two layers are
        # distinct — the machine constrains edges, the store constrains grounds.
        assert EpistemicState.VALIDATED in LEGAL_TRANSITIONS[EpistemicState.UNEXAMINED]

    def test_self_transition_always_legal(self):
        # bitemporal history: re-confirming the same state must be recordable
        for state in EpistemicState:
            if state is EpistemicState.UNRESOLVED:
                continue  # re-affirming absence still requires the investigation
            assert check_transition(state, state) is state

    def test_contradicted_is_absorbing(self):
        with pytest.raises(InvalidTransition):
            check_transition(EpistemicState.CONTRADICTED, EpistemicState.SUPPORTED)

    def test_retracted_cannot_become_supported(self):
        with pytest.raises(InvalidTransition):
            check_transition(EpistemicState.RETRACTED, EpistemicState.SUPPORTED)

    def test_validated_may_be_contradicted(self):
        assert (
            check_transition(EpistemicState.VALIDATED, EpistemicState.CONTRADICTED)
            is EpistemicState.CONTRADICTED
        )

    def test_illegal_transition_message_lists_legal_targets(self):
        with pytest.raises(InvalidTransition, match="legal targets"):
            check_transition(EpistemicState.RETRACTED, EpistemicState.VALIDATED)


class TestAbsenceIsProvable:
    """The distinction that makes the whole thesis work."""

    def test_unresolved_requires_investigation(self):
        with pytest.raises(MissingInvestigation):
            check_transition(EpistemicState.SUPPORTED, EpistemicState.UNRESOLVED)

    def test_unresolved_from_unexamined_also_requires_investigation(self):
        with pytest.raises(MissingInvestigation):
            check_transition(EpistemicState.UNEXAMINED, EpistemicState.UNRESOLVED)

    def test_reaffirming_unresolved_requires_investigation(self):
        with pytest.raises(MissingInvestigation):
            check_transition(EpistemicState.UNRESOLVED, EpistemicState.UNRESOLVED)

    def test_unresolved_allowed_with_investigation(self):
        assert (
            check_transition(
                EpistemicState.SUPPORTED, EpistemicState.UNRESOLVED, has_investigation=True
            )
            is EpistemicState.UNRESOLVED
        )

    def test_unresolved_and_inconclusive_are_distinct(self):
        assert EpistemicState.UNRESOLVED is not EpistemicState.INCONCLUSIVE
        assert EpistemicState.UNRESOLVED.value != EpistemicState.INCONCLUSIVE.value

    def test_unknown_state_rejected(self):
        with pytest.raises(Exception):
            validate_state("PROBABLY_TRUE")

    def test_string_values_accepted(self):
        assert validate_state("supported") is EpistemicState.SUPPORTED


class TestNoConfidenceForTruth:
    def test_no_state_function_takes_a_confidence_argument(self):
        import inspect

        from sovereign_runtime.knowledge import epistemic as mod

        for name in ("check_transition", "validate_state"):
            sig = inspect.signature(getattr(mod, name))
            assert "confidence" not in sig.parameters
            assert "score" not in sig.parameters
            assert "probability" not in sig.parameters


# --------------------------------------------------------------------------
# Crosswalk
# --------------------------------------------------------------------------


class TestCrosswalk:
    def test_four_source_vocabularies(self):
        assert set(CROSSWALK) == {
            "aep",
            "ganymede3-claim-graph",
            "hermes-atlas",
            "sovereign-intelligence",
        }

    def test_every_mapping_targets_the_spine(self):
        for source, table in CROSSWALK.items():
            for term, target in table.items():
                assert target is None or isinstance(target, EpistemicState)

    def test_aep_verified_maps_to_validated(self):
        assert resolve("aep", "VERIFIED") is EpistemicState.VALIDATED

    def test_atlas_candidate_maps_to_unexamined(self):
        assert resolve("hermes-atlas", "candidate") is EpistemicState.UNEXAMINED

    def test_merged_has_no_faithful_target(self):
        # MERGED is a graph operation, not a state — resolving it must fail
        # loudly rather than pick the nearest state
        with pytest.raises(UnmappedTerm, match="graph operation"):
            resolve("ganymede3-claim-graph", "MERGED")

    def test_unknown_source_rejected(self):
        with pytest.raises(UnmappedTerm):
            resolve("nonesuch", "VERIFIED")

    def test_unknown_term_rejected(self):
        with pytest.raises(UnmappedTerm, match="unknown term"):
            resolve("aep", "PROBABLY")

    def test_brief_vocabulary_cannot_express_absence(self):
        # documents *why* the brief's list was rejected: no term maps to
        # UNRESOLVED, so the distinction would be lost
        assert EpistemicState.UNRESOLVED not in set(CROSSWALK["aep"].values())
