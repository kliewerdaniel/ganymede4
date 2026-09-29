"""Corpus loader tests (ADR-010).

Two things are being tested here, and the second matters more.

The first is that the loader reads the export correctly. Straightforward, and
mostly covered by the real-corpus run.

The second is **speaker attribution**, because this corpus's defining property
is that 44% of it is a language model's prior output. If a `chatgpt_assistant`
turn is ever typed as testimony, the compiler will produce a claim with a
perfect source span and a Merkle root that launders a model's assertion into
the historical record — the exact failure this project exists to detect in
other people's systems, and it would be undetectable from the artifact alone,
because every structural guarantee would still hold.

So these tests assert the typing, not just the parsing. A loader regression
that flipped one type would pass every provenance check in the codebase and
silently corrupt the meaning of the corpus.
"""

from __future__ import annotations

import pytest

from sovereign_runtime.corpus import load as loader
from sovereign_runtime.corpus.load import (
    AUTHOR,
    MACHINE_TYPES,
    TESTIMONY_TYPES,
    corpus_stats,
    load_corpus,
)


# --------------------------------------------------------------------------
# the corpus is real and reachable
# --------------------------------------------------------------------------


class TestRealCorpus:
    def test_the_corpus_is_present_and_large(self):
        """Not a fixture. If the record moves, this fails loudly rather than
        quietly testing against a stub."""
        stats = corpus_stats(limit=400)
        assert stats["documents"] > 0
        assert stats["bytes"] > 0

    def test_testimony_and_machine_documents_both_appear(self):
        """A loader that only found one kind would pass a type test."""
        seen = {d.source_type for d in load_corpus(limit=3000)}
        assert "chatgpt_user" in seen
        assert "chatgpt_assistant" in seen
        assert "reddit_comment" in seen

    def test_every_document_has_content_and_a_digest(self):
        for doc in load_corpus(limit=500):
            assert doc.content.strip(), f"{doc.uri} is empty"
            assert len(doc.digest) == 64

    def test_digests_are_stable_across_runs(self):
        """Re-ingesting an unchanged corpus must be a no-op.

        The compiler's whole reproducibility story rests on this, and a loader
        that produced a fresh digest per run would make recompilation look
        like a change.
        """
        first = [d.digest for d in load_corpus(limit=800)]
        second = [d.digest for d in load_corpus(limit=800)]
        assert first == second

    def test_uris_are_unique(self):
        """Duplicate uris would collapse distinct documents into one source."""
        uris = [d.uri for d in load_corpus(limit=2000)]
        assert len(uris) == len(set(uris))


# --------------------------------------------------------------------------
# speaker attribution — the point of the whole loader
# --------------------------------------------------------------------------


class TestSpeakerAttribution:
    def test_machine_output_is_never_typed_as_testimony(self):
        """The load-bearing assertion of ADR-010.

        A `chatgpt_assistant` turn typed as testimony would become a compiled
        claim whose evidence span points at a model's assertion. Every
        structural guarantee in the compiler would still hold. Nothing
        downstream could detect it from the artifact. So it is checked here, at
        the only place the information still exists.
        """
        for doc in load_corpus(limit=4000):
            assert not (
                doc.source_type in TESTIMONY_TYPES and "assistant" in doc.source_type
            ), f"{doc.uri} typed as testimony but is model output"
            assert not (
                doc.source_type in TESTIMONY_TYPES
                and doc.source_type.startswith("chatgpt_assistant")
            )

    def test_the_two_type_sets_are_disjoint(self):
        assert not (TESTIMONY_TYPES & MACHINE_TYPES)
        assert "chatgpt_assistant" in MACHINE_TYPES
        assert "chatgpt_user" in TESTIMONY_TYPES

    def test_reddit_documents_carry_their_metadata(self):
        """Provenance-adjacent: a reader can check where a claim came from
        without trusting the loader."""
        for doc in load_corpus(limit=600):
            if doc.source_type.startswith("reddit_"):
                assert doc.meta.get("permalink"), f"{doc.uri} has no permalink"
                assert doc.meta.get("created_utc"), f"{doc.uri} has no timestamp"

    def test_other_authors_are_not_credited_to_the_account(self):
        """The export contains other people's words as quoted context.

        A post by another account that appears in the export is not this
        account's testimony, and typing it as such would attribute someone
        else's statement — in this corpus, sometimes a distressed person's — to
        KonradFreeman.
        """
        found = [d for d in load_corpus(limit=4000) if d.source_type.endswith("_other")]
        for doc in found:
            assert doc.meta.get("author") != AUTHOR
            assert doc.source_type not in TESTIMONY_TYPES

    def test_conversation_turns_carry_their_conversation_id(self):
        """Turns are split per-conversation, so a claim can name where in the
        history it came from."""
        for doc in load_corpus(limit=1500):
            if doc.source_type.startswith("chatgpt_"):
                assert doc.meta.get("conversation_id")

    def test_export_scaffolding_is_stripped(self):
        """`---` rules and timestamp lines are export formatting, not prose.

        Left in, they become claim text and pollute the lexical index with
        tokens that mean nothing.
        """
        for doc in load_corpus(limit=1500):
            assert "**Timestamp**" not in doc.content
            assert not doc.content.strip().startswith("---")


# --------------------------------------------------------------------------
# the frontmatter parser — deliberately not a YAML parser
# --------------------------------------------------------------------------


class TestFrontmatter:
    def test_it_parses_a_flat_block(self):
        meta, body = loader._split_frontmatter("---\na: 1\nb: 'two'\n---\nbody here")
        assert meta == {"a": "1", "b": "two"}
        assert body.strip() == "body here"

    def test_it_tolerates_a_missing_block(self):
        meta, body = loader._split_frontmatter("no frontmatter here")
        assert meta == {}
        assert body == "no frontmatter here"

    def test_it_ignores_nested_structures_rather_than_guessing(self):
        """Only flat top-level scalars are accepted, and it stops at anything else.

        Pulling in a YAML library for this would break the stdlib-only rule,
        and would also be wrong: silently accepting arbitrary YAML from a file
        about to become provenance is more power than the job requires.

        Stopping rather than skipping is the part that matters. A skip reads a
        nested key as a top-level one while claiming not to understand the
        structure, so the caller cannot tell the shape was misread — and this
        is metadata that becomes permalinks and timestamps.
        """
        meta, _ = loader._split_frontmatter(
            "---\nplain: yes\nnested:\n  key: value\nlater: nope\n---\nbody"
        )
        assert meta.get("plain") == "yes"
        assert meta.get("nested") == ""
        assert "key" not in meta, "a nested key was read as top-level"
        assert "later" not in meta, "parsing continued past a structure it cannot model"

    def test_it_stops_at_a_list_item(self):
        meta, _ = loader._split_frontmatter("---\na: 1\nitems:\n  - x\nb: 2\n---\nbody")
        assert meta.get("a") == "1"
        assert "b" not in meta

    def test_it_handles_a_document_with_no_trailing_newline(self):
        meta, body = loader._split_frontmatter("---\na: 1\n---\nlast line")
        assert meta == {"a": "1"}
        assert body.strip() == "last line"


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------


class TestDeterminism:
    def test_iteration_order_is_stable(self):
        assert [d.uri for d in load_corpus(limit=300)] == [
            d.uri for d in load_corpus(limit=300)
        ]

    def test_limit_truncates_deterministically(self):
        assert [d.uri for d in load_corpus(limit=50)] == [
            d.uri for d in load_corpus(limit=50)
        ]

    def test_the_corpus_digest_covers_content_not_mtimes(self):
        """Two stat runs over an unchanged corpus must agree.

        Otherwise a rebuild would look like a change and produce a new Merkle
        root for no reason, which would make the artifact's identity a
        function of when you looked at it.
        """
        assert corpus_stats(limit=300)["corpus_digest"] == corpus_stats(limit=300)[
            "corpus_digest"
        ]
