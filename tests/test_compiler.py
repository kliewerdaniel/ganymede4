"""Compiler tests: determinism, the state wall, and the manifest.

Gate 2 of ``migration-plan-and-test-strategy.md``: full equality and delta
isolation both green, a Merkle root present, and ``diff`` between two identical
compiles empty.

The tests here are written to fail on the properties that actually matter —
reproducibility, order-independence, and the compiler's inability to assert
certainty — rather than to pin implementation details.
"""

from __future__ import annotations

import pytest

from sovereign_runtime.compile.compiler import (
    COMPILE_PERMITTED_STATES,
    HEURISTIC_MIN_SUPPORT,
    SourceSpec,
    _forbid_state_assignment,
    compile_corpus,
)
from sovereign_runtime.compile.manifest import build_manifest, merkle_root
from sovereign_runtime.compile.segment import iter_segments, segment
from sovereign_runtime.knowledge.epistemic import EpistemicState
from sovereign_runtime.knowledge.store import Store

TT = "2026-09-28T00:00:00Z"

CORPUS_A = "The runtime is local-first. Provenance is enforced at write time."
CORPUS_B = "Daniel is the author. The runtime is local-first. Provenance is enforced at write time."
CORPUS_C = "A third note exists here. Nothing recurs here."


def _three_recurring() -> list[SourceSpec]:
    """Three sources sharing a sentence shape, to trip the miner."""
    shared = "The runtime is local-first. Provenance is enforced at write time."
    return [SourceSpec(uri=f"{i}.md", content=f"{shared} Tail number {i}.") for i in range(3)]


# -- segmentation --------------------------------------------------------


class TestSegmentation:
    def test_every_segment_is_verbatim(self):
        text = "One thing. Another thing! A third?"
        for seg in segment(text):
            assert text[seg.start : seg.end] == seg.text

    def test_offsets_are_ordered_and_non_overlapping(self):
        text = "Alpha beta. Gamma delta. Epsilon."
        segs = segment(text)
        assert [s.ordinal for s in segs] == [0, 1, 2]
        for prev, nxt in zip(segs, segs[1:]):
            assert prev.end <= nxt.start

    def test_abbreviation_does_not_split(self):
        text = "Dr. Smith arrived. He was late."
        segs = segment(text)
        assert len(segs) == 2
        assert segs[0].text == "Dr. Smith arrived."

    def test_ellipsis_does_not_split(self):
        text = "Wait... what happened? Nothing."
        segs = segment(text)
        assert segs[0].text == "Wait..."
        assert segs[1].text == "what happened?"

    def test_closing_quote_stays_attached(self):
        # The whole quoted utterance is one segment, closing quote included —
        # the failure mode being avoided is '"no"' splitting off and leaving a
        # bare '"' glued to the following sentence.
        text = 'He said "no." Then he left.'
        segs = segment(text)
        assert segs[0].text == 'He said "no."'
        assert segs[1].text == "Then he left."
        assert all(seg.text.strip('"') for seg in segs)

    def test_whitespace_is_trimmed_from_bounds(self):
        text = "First line.\n\n  Second line.   \n"
        for seg in segment(text):
            assert seg.text == seg.text.strip()
            assert not seg.text.startswith(" ")

    def test_empty_and_whitespace_only_text_yields_nothing(self):
        assert segment("") == []
        assert segment("   \n\n  ") == []
        assert list(iter_segments("")) == []

    def test_text_without_terminator_is_one_segment(self):
        segs = segment("no terminator here")
        assert len(segs) == 1
        assert segs[0].text == "no terminator here"

    def test_unordered_offsets_are_rejected(self):
        from sovereign_runtime.compile.segment import Segment

        with pytest.raises(ValueError):
            Segment(text="x", start=5, end=2, ordinal=0)


# -- the state wall (ADR-004 §1) ----------------------------------------


class TestCompilerStateWall:
    def test_compiler_permits_only_unexamined(self):
        assert COMPILE_PERMITTED_STATES == frozenset({EpistemicState.UNEXAMINED})

    def test_guard_fires_if_the_wall_is_widened(self, monkeypatch):
        import sovereign_runtime.compile.compiler as mod

        monkeypatch.setattr(
            mod, "COMPILE_PERMITTED_STATES", frozenset(EpistemicState)
        )
        with pytest.raises(RuntimeError, match="ADR-004 violation"):
            _forbid_state_assignment()

    def test_no_compiled_claim_is_in_a_certifying_state(self):
        with Store() as store:
            art = compile_corpus(
                store,
                [SourceSpec(uri="a.md", content=CORPUS_A)],
                transaction_time=TT,
            )
            states = {row["state"] for row in store.claims()}
        assert states <= {EpistemicState.UNEXAMINED.value}
        assert art.manifest.state_counts.get("supported") is None
        assert art.manifest.state_counts.get("validated") is None

    def test_compiler_never_emits_unresolved(self):
        """UNRESOLVED asserts a search happened; a compile is not a search."""
        with Store() as store:
            art = compile_corpus(
                store,
                [SourceSpec(uri="a.md", content=CORPUS_A)],
                transaction_time=TT,
            )
        assert "unresolved" not in art.manifest.state_counts

    def test_no_reading_of_a_confidence_value(self):
        """ADR-002 §1: nothing in the compile path may consult confidence.

        Checked structurally rather than by eye: every executable line of the
        compile package is scanned for the identifier. Docstrings and comments
        are skipped because the *rationale* legitimately names the thing being
        refused. A grep by a human is exactly the check that silently stops
        being run; this one runs in CI.
        """
        import ast
        import sovereign_runtime.compile.compiler as mod
        import sovereign_runtime.compile.manifest as man
        import sovereign_runtime.compile.segment as seg

        banned = {"confidence", "conf", "score", "prob", "probability"}
        for module in (mod, man, seg):
            path = module.__file__
            assert path is not None, f"{module.__name__} has no source file"
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    assert node.id.lower() not in banned, (
                        f"{module.__name__}:{node.lineno} reads {node.id}"
                    )
                if isinstance(node, ast.Attribute):
                    assert node.attr.lower() not in banned, (
                        f"{module.__name__}:{node.lineno} reads .{node.attr}"
                    )
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    # A bare string key would smuggle a confidence value through.
                    assert node.value.lower() not in banned, (
                        f"{module.__name__}:{node.lineno} carries {node.value!r}"
                    )


# -- determinism (Gate 2) ------------------------------------------------


class TestFullEquality:
    def test_same_bytes_give_byte_identical_manifest(self):
        specs = _three_recurring()
        with Store() as s1:
            a = compile_corpus(s1, specs, transaction_time=TT)
        with Store() as s2:
            b = compile_corpus(s2, specs, transaction_time=TT)
        assert a.manifest.root == b.manifest.root
        assert a.manifest.to_bytes() == b.manifest.to_bytes()

    def test_root_is_independent_of_ingestion_order(self):
        specs = _three_recurring()
        with Store() as s1:
            a = compile_corpus(s1, specs, transaction_time=TT)
        with Store() as s2:
            b = compile_corpus(s2, list(reversed(specs)), transaction_time=TT)
        assert a.manifest.root == b.manifest.root

    def test_provenance_fields_do_not_affect_the_root(self):
        """compiled_at and run_id are recorded but never hashed."""
        specs = _three_recurring()
        with Store() as s1:
            a = compile_corpus(
                s1, specs, transaction_time=TT, compiled_at="2026-01-01T00:00:00Z", run_id="r1"
            )
        with Store() as s2:
            b = compile_corpus(
                s2, specs, transaction_time=TT, compiled_at="2031-12-31T23:59:59Z", run_id="r99"
            )
        assert a.manifest.root == b.manifest.root
        # ...but they are genuinely recorded.
        assert a.manifest.compiled_at != b.manifest.compiled_at
        assert a.manifest.run_id != b.manifest.run_id

    def test_recompiling_the_same_corpus_is_a_no_op(self):
        specs = _three_recurring()
        with Store() as store:
            first = compile_corpus(store, specs, transaction_time=TT)
            before = store.counts()
            second = compile_corpus(store, specs, transaction_time=TT)
            after = store.counts()
        assert first.manifest.root == second.manifest.root
        assert before == after, "recompiling identical bytes wrote to the store"

    def test_one_byte_change_produces_a_new_version(self):
        specs = _three_recurring()
        changed = list(specs)
        changed[0] = SourceSpec(uri="0.md", content=specs[0].content.replace("Tail number 0", "Tail 0"))
        with Store() as s1:
            a = compile_corpus(s1, specs, transaction_time=TT)
        with Store() as s2:
            b = compile_corpus(s2, changed, transaction_time=TT)
        assert a.manifest.root != b.manifest.root

    def test_version_is_stable_across_processes(self, tmp_path):
        """A rebuild in a fresh interpreter must produce the same version.

        This is the check that distinguishes "reproducible" from "reproducible
        in this process": hash randomization, dict ordering, and interpreter
        state are all per-process, so only a second process can prove they do
        not leak into the version.
        """
        import pathlib
        import subprocess
        import sys

        repo = pathlib.Path(__file__).resolve().parent.parent
        script = (
            "import sys;"
            f"sys.path.insert(0, {str(repo / 'src')!r});"
            "from sovereign_runtime.compile.compiler import compile_corpus, SourceSpec;"
            "from sovereign_runtime.knowledge.store import Store;"
            f"spec=SourceSpec(uri='a.md', content={CORPUS_A!r});"
            f"print(compile_corpus(Store({str(tmp_path / 'x.db')!r}), [spec],"
            " transaction_time='t').manifest.root)"
        )
        runs = [
            subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            for _ in range(2)
        ]
        assert runs[0] == runs[1]
        with Store() as store:
            inproc = compile_corpus(
                store, [SourceSpec(uri="a.md", content=CORPUS_A)], transaction_time="t"
            )
        assert inproc.manifest.root == runs[0]


class TestDeltaIsolation:
    def test_recompiling_a_subset_leaves_other_records_untouched(self):
        """Unchanged sources keep their ids; a changed one gets a new id."""
        with Store() as store:
            base = compile_corpus(
                store,
                [SourceSpec(uri="a.md", content=CORPUS_A), SourceSpec(uri="b.md", content=CORPUS_B)],
                transaction_time=TT,
            )
            kept_sid, edited_sid = base.source_ids  # input-ordered tuple
            after = compile_corpus(
                store,
                [
                    SourceSpec(uri="a.md", content=CORPUS_A),  # unchanged
                    SourceSpec(uri="b.md", content=CORPUS_B + " Added later."),  # changed
                ],
                transaction_time=TT,
            )
            # The unchanged source's id survives.
            assert kept_sid in after.source_ids
            # The changed source's id does not.
            assert edited_sid not in after.source_ids
            # Every claim id from the first compile still resolves to a row.
            # Delta isolation means an edit *adds*; it never silently
            # invalidates or rewrites the records that did not change.
            # Checked inside the `with`, since the store is closed after it.
            for cid in base.claim_ids:
                assert store.get_claim(cid) is not None
        assert after.manifest.root != base.manifest.root

    def test_diff_between_identical_compiles_is_empty(self):
        specs = _three_recurring()
        with Store() as s1:
            a = compile_corpus(s1, specs, transaction_time=TT)
        with Store() as s2:
            b = compile_corpus(s2, specs, transaction_time=TT)

        def leaves(m):
            return set(m.claim_ids) | set(m.evidence_ids) | set(m.source_ids) | set(m.heuristic_ids)

        assert leaves(a.manifest) - leaves(b.manifest) == set()
        assert leaves(b.manifest) - leaves(a.manifest) == set()

    def test_diff_identifies_exactly_the_changed_leaves(self):
        """The changed source's leaves are new; the untouched ones are stable.

        Note this asserts *which* leaves appear, not a count of 2. Appending to
        source 0 also changes the miner's view of the corpus, so a recurring
        shape can gain a supporting claim and therefore get a new
        content-addressed id of its own. The property that matters — and the one
        that would break first under a real regression — is that the untouched
        sources keep their identity.
        """
        specs = _three_recurring()
        with Store() as s1:
            a = compile_corpus(s1, specs, transaction_time=TT)
            edited_sid = a.source_ids[0]  # positional: this tuple is input-ordered
            untouched_sids = set(a.source_ids[1:])
        changed = list(specs)
        changed[0] = SourceSpec(uri="0.md", content=specs[0].content + " Extra sentence here.")
        with Store() as s2:
            b = compile_corpus(s2, changed, transaction_time=TT)

        # The untouched sources keep byte-identical ids. Compared as sets
        # against ids captured by position from the input-ordered tuple:
        # `manifest.source_ids` is sorted, so indexing it does NOT identify
        # source 0 — a mistake the first version of this test made twice.
        assert untouched_sids <= set(b.manifest.source_ids)
        # The edited source gets a new id rather than mutating in place.
        assert edited_sid not in set(b.manifest.source_ids)

        def leaves(m):
            return set(m.claim_ids) | set(m.evidence_ids) | set(m.source_ids)

        added = leaves(b.manifest) - leaves(a.manifest)
        removed = leaves(a.manifest) - leaves(b.manifest)
        assert added, "the edit produced no new leaves at all"

        # A leaf belonging to an untouched source must never appear in
        # `removed`. Recompiling only sources 1 and 2 yields their leaf set, so
        # this is checked against ground truth rather than against a guess
        # about which claims "belonged" where.
        with Store() as s3:
            only_untouched = compile_corpus(s3, specs[1:], transaction_time=TT)
        untouched_leaves = leaves(only_untouched.manifest)
        assert not (removed & untouched_leaves), (
            "records from untouched sources were dropped by the edit"
        )
        # Their claims all still resolve in the changed artifact too.
        for cid in only_untouched.manifest.claim_ids:
            assert cid in set(b.manifest.claim_ids)


# -- the miner -----------------------------------------------------------


class TestHeuristicMiner:
    def test_miner_emits_at_threshold(self):
        with Store() as store:
            art = compile_corpus(store, _three_recurring(), transaction_time=TT)
        assert len(art.heuristic_ids) == 2

    def test_miner_stays_silent_below_threshold(self):
        specs = _three_recurring()[: HEURISTIC_MIN_SUPPORT - 1]
        with Store() as store:
            art = compile_corpus(store, specs, transaction_time=TT)
        assert art.heuristic_ids == ()

    def test_heuristics_are_claims_in_the_graph(self):
        with Store() as store:
            art = compile_corpus(store, _three_recurring(), transaction_time=TT)
            assert art.heuristic_ids
            for hid in art.heuristic_ids:
                row = store.get_claim(hid)
                assert row is not None, f"heuristic {hid} is not a claim row"
                assert row["state"] == EpistemicState.DERIVED.value

    def test_heuristic_state_is_derivable_and_can_be_contradicted(self):
        """A heuristic is not an oracle: it can be walked to CONTRADICTED."""
        with Store() as store:
            art = compile_corpus(store, _three_recurring(), transaction_time=TT)
            assert art.heuristic_ids
            hid = art.heuristic_ids[0]
            store.set_state(hid, EpistemicState.CONTRADICTED, transaction_time=TT)
            row = store.get_claim(hid)
            assert row is not None
            assert row["state"] == EpistemicState.CONTRADICTED.value

    def test_recurrence_requires_distinct_sources(self):
        """The same source listed twice is not two independent statements."""
        spec = SourceSpec(uri="a.md", content=CORPUS_A)
        with Store() as store:
            art = compile_corpus(store, [spec, spec], transaction_time=TT)
        assert art.heuristic_ids == ()

    def test_miner_output_is_deterministic_across_orders(self):
        specs = _three_recurring()
        with Store() as s1:
            a = compile_corpus(s1, specs, transaction_time=TT)
        with Store() as s2:
            b = compile_corpus(s2, list(reversed(specs)), transaction_time=TT)
        assert set(a.heuristic_ids) == set(b.heuristic_ids)

    def test_recurring_sentence_is_stated_as_a_count_not_a_conclusion(self):
        with Store() as store:
            art = compile_corpus(store, _three_recurring(), transaction_time=TT)
            assert art.heuristic_ids
            texts = []
            for h in art.heuristic_ids:
                row = store.get_claim(h)
                assert row is not None
                texts.append(row["text"])
        for text in texts:
            assert "occurs in" in text
            assert "is true" not in text
            assert "proves" not in text


# -- manifest / merkle ---------------------------------------------------


class TestManifest:
    def test_root_is_order_independent_and_dedupes(self):
        assert merkle_root(["b", "a", "c"]) == merkle_root(["a", "b", "c", "a"])

    def test_empty_corpus_has_a_distinct_stable_root(self):
        assert merkle_root([]) == merkle_root([])
        assert merkle_root([]) != merkle_root(["a"])

    def test_edge_count_participates_in_the_root(self):
        """Same claim ids, different edges, different artifact."""
        a = build_manifest(source_ids=["s"], claim_ids=["c"], evidence_ids=["e"], edge_count=0)
        b = build_manifest(source_ids=["s"], claim_ids=["c"], evidence_ids=["e"], edge_count=1)
        assert a.root != b.root

    def test_schema_version_participates_in_the_root(self):
        import sovereign_runtime.compile.manifest as man

        a = build_manifest(source_ids=["s"], claim_ids=["c"], evidence_ids=["e"])
        original = man.MANIFEST_SCHEMA_VERSION
        try:
            man.MANIFEST_SCHEMA_VERSION = original + 1
            b = man.build_manifest(source_ids=["s"], claim_ids=["c"], evidence_ids=["e"])
        finally:
            man.MANIFEST_SCHEMA_VERSION = original
        assert a.root != b.root

    def test_provenance_is_separated_from_rooted_fields(self):
        m = build_manifest(
            source_ids=["s"],
            claim_ids=["c"],
            evidence_ids=["e"],
            compiled_at="2026-01-01T00:00:00Z",
            run_id="r1",
        )
        d = m.to_dict()
        assert "compiled_at" in d["provenance"]
        assert "run_id" in d["provenance"]
        assert "compiled_at" not in d["rooted"]

    def test_version_string_is_stable(self):
        m = build_manifest(source_ids=["s"], claim_ids=["c"], evidence_ids=["e"])
        assert m.version == f"v1-{m.root[:16]}"


# -- provenance survives compilation ------------------------------------


class TestCompiledProvenance:
    def test_every_compiled_claim_resolves_to_an_offset(self):
        with Store() as store:
            art = compile_corpus(
                store,
                [SourceSpec(uri="a.md", content=CORPUS_A)],
                transaction_time=TT,
            )
            for cid in art.claim_ids:
                evs = store.evidence_for(cid)
                assert evs, f"claim {cid} has no evidence"
                for ev in evs:
                    resolved = store.resolve_span(ev["id"])
                    assert resolved is not None, f"evidence {ev['id']} does not resolve"
                    _uri, start, end = resolved
                    src = store.get_source(ev["source_id"])
                    assert src is not None
                    assert src["content"][start:end] == ev["text"]

    def test_claim_text_is_always_a_substring_of_its_source(self):
        with Store() as store:
            art = compile_corpus(
                store,
                [SourceSpec(uri="a.md", content=CORPUS_A), SourceSpec(uri="b.md", content=CORPUS_B)],
                transaction_time=TT,
            )
            for cid in art.claim_ids:
                row = store.get_claim(cid)
                assert row is not None
                ev = store.evidence_for(cid)[0]
                assert row["text"] == ev["text"]
                src = store.get_source(ev["source_id"])
                assert src is not None
                assert row["text"] in src["content"]

    def test_summary_is_serializable(self):
        import json

        with Store() as store:
            art = compile_corpus(store, _three_recurring(), transaction_time=TT)
        json.dumps(art.summary())
