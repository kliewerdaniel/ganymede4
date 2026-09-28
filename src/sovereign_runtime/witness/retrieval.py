"""Deterministic lexical retrieval (BM25).

Stdlib only, no model, no index server. This is the retrieval tier the witness
uses, and its limits are not incidental — they are disclosed.

Why BM25 and not embeddings, stated plainly: an embedding ranker produces
similarity scores, and similarity is not entailment. The measured form of that
gap is in ``migration-map.md``: cross-encoder and RRF score distributions fully
overlap between answerable and answer-absent cases. So a vector ranker would
make ``UNRESOLVED`` look better-founded than it is while changing nothing about
whether the answer is present.

What BM25 *can* do honestly is answer "which claims share the most distinctive
terms with this query", and that is exactly the claim the witness is permitted
to make (ADR-005 §5). The rank is exposed on every result so a caller can see
it was lexical.

Determinism: no randomness, no dict-order dependence, no floating-point
accumulation whose order could vary across platforms (scores are computed from
sorted term lists, and ties break on the content id). The same corpus and query
give the same ranking in any process, on any machine.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

__all__ = ["BM25", "ScoredDoc", "tokenize", "METHOD_ID"]

#: The method identifier recorded in every investigation. A future semantic
#: retriever adds a *different* id rather than replacing this one, so an
#: ``UNRESOLVED`` from a lexical search stays distinguishable from an
#: ``UNRESOLVED`` from a semantic one (ADR-005 §4).
METHOD_ID = "lexical-bm25"

#: Standard BM25 constants. k1 controls term-frequency saturation, b controls
#: length normalization. These are the well-established defaults, not tuned
#: values — tuning them against a test set would be exactly the
#: threshold-fitting this project refuses elsewhere.
K1 = 1.5
B = 0.75

#: English function words, dropped at both index and query time.
#:
#: This is a stopword list, and stopword lists are normally a smell — they are a
#: semantic decision smuggled in as configuration. It is here anyway, for two
#: reasons that make it narrower than it first appears:
#:
#: 1. It is explicit and small, so its effect is auditable in a diff rather
#:    than loaded from a corpus-frequency table whose provenance nobody can
#:    reconstruct.
#: 2. Every idf is positive in this implementation, so a function word
#:    contributes real score. Without this list "is" alone retrieves documents.
#:
#: The entries that carry *meaning* are deliberately ABSENT, because dropping
#: them would make the witness confidently wrong:
#:
#:   not, no, never, none, without  — negation. "the runtime is not
#:     local-first" and "the runtime is local-first" must not retrieve
#:     identically, or the witness will answer a yes-question with a
#:     no-claim's citation.
#:   all, always, only, most, least, more, less  — quantifiers that change
#:     what a claim asserts.
#:   before, after, during, since, until  — temporal operators. Removing these
#:     would make "X did not happen before Y" and "X happened after Y"
#:     indistinguishable.
#:
#: Dropping a negation word is worse than returning a weak answer: it converts
#: a hedge into a claim.
FUNCTION_WORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "then", "than", "as",
        "of", "at", "by", "for", "with", "about", "into", "from", "to",
        "in", "on", "is", "are", "was", "were", "be", "been", "being",
        "am", "do", "does", "did", "done", "have", "has", "had", "having",
        "it", "its", "this", "that", "these", "those", "there", "here",
        "which", "who", "whom", "whose", "what", "how", "when", "where",
        "why", "so", "such", "very", "too", "also", "s", "t", "re", "ve",
        "ll", "d", "m", "just",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop function words.

    Deterministic and dependency-free, so the same string always produces the
    same token list. Negation and quantifiers survive (see ``FUNCTION_WORDS``).
    """
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in FUNCTION_WORDS]


@dataclass(frozen=True)
class ScoredDoc:
    """One retrieved record and why it matched."""

    doc_id: str
    score: float
    #: Terms that actually matched. Exposed because a score with no
    #: explanation is not auditable — a caller must be able to see *why* a
    #: claim surfaced, which is a precondition for trusting a `UNRESOLVED`.
    matched_terms: tuple[str, ...]
    text: str = ""


class BM25:
    """A BM25 index over a fixed set of documents.

    Built once over the compiled claims, queried many times. Adding documents
    requires a rebuild rather than an incremental update, which is the correct
    trade here: the artifact is immutable and versioned, so the index is derived
    state and rebuilding it is cheap and obviously correct.
    """

    def __init__(self, documents: Mapping[str, str], *, k1: float = K1, b: float = B) -> None:
        self.k1 = k1
        self.b = b
        self._docs: dict[str, str] = dict(documents)
        self._tokens: dict[str, list[str]] = {d: tokenize(t) for d, t in self._docs.items()}
        self._tf: dict[str, Counter[str]] = {d: Counter(t) for d, t in self._tokens.items()}
        self._len: dict[str, int] = {d: len(t) for d, t in self._tokens.items()}
        self._avg_len = (sum(self._len.values()) / len(self._len)) if self._len else 0.0
        df: Counter[str] = Counter()
        for counts in self._tf.values():
            df.update(counts.keys())
        n = len(self._docs)
        # Okapi BM25's idf: log((N - df + 0.5)/(df + 0.5) + 1), which is always
        # positive. An earlier revision floored the *Robertson* form at zero
        # instead, and that was wrong twice over: in a four-document corpus
        # half the vocabulary has df >= N/2, so the real term "provenance"
        # scored 0 and the artifact stopped being queryable. Small corpora are
        # the normal case for this project, not an edge case.
        #
        # Because every idf is now positive, the guarantee that keeps function
        # words out has to come from tokenization, not from the weighting. See
        # FUNCTION_WORDS in `tokenize`.
        self._idf: dict[str, float] = {
            term: math.log((n - freq + 0.5) / (freq + 0.5) + 1.0)
            for term, freq in df.items()
        }
        self._df = df

    def __len__(self) -> int:
        return len(self._docs)

    def search(self, query: str, limit: int = 10) -> list[ScoredDoc]:
        """Return up to ``limit`` documents, best first.

        Ties break on doc id so the ordering is total and reproducible; without
        that, two equally-scored claims would swap places between runs and
        ``diff`` on the answer would be noise.
        """
        terms = tokenize(query)
        if not terms or not self._docs:
            return []
        results: list[ScoredDoc] = []
        for doc_id in sorted(self._docs):
            score = 0.0
            matched: list[str] = []
            tf = self._tf[doc_id]
            dl = self._len[doc_id]
            # Iterate the query's *unique* terms in sorted order: summing in a
            # fixed order keeps floating-point accumulation identical across
            # runs and platforms.
            for term in sorted(set(terms)):
                freq = tf.get(term, 0)
                if not freq:
                    continue
                idf = self._idf.get(term, 0.0)
                denom = freq + self.k1 * (1.0 - self.b + self.b * dl / (self._avg_len or 1.0))
                score += idf * (freq * (self.k1 + 1.0)) / (denom or 1.0)
                matched.append(term)
            if matched and score > 0.0:
                # `score > 0` is the substantive filter, not a guard against
                # float noise: a document matching only terms with zero
                # discriminative power (present in every document) has not been
                # retrieved, it has merely been mentioned. Returning it would
                # turn a nonsense query into a confident-looking citation, and
                # that is the failure mode that makes a witness untrustworthy.
                results.append(
                    ScoredDoc(
                        doc_id=doc_id,
                        score=score,
                        matched_terms=tuple(matched),
                        text=self._docs[doc_id],
                    )
                )
        results.sort(key=lambda d: (-d.score, d.doc_id))
        return results[:limit]

    def contains_any_term(self, text: str) -> bool:
        """True if any token of ``text`` appears anywhere in the index."""
        probe = set(tokenize(text))
        return bool(probe & set(self._idf))

    def describe(self) -> dict[str, Any]:
        """Index statistics, for the witness boundary."""
        return {
            "method": METHOD_ID,
            "documents": len(self._docs),
            "vocabulary": len(self._idf),
            "avg_doc_len": round(self._avg_len, 4),
            "k1": self.k1,
            "b": self.b,
        }
