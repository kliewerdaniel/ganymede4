"""The corpus: the author's own record (ADR-010).

The demo corpus is KonradFreeman's Reddit account and ChatGPT conversation
history — 3,416 documents, read from their existing location on this machine
and never committed to the repository.

It is personal: it contains the author's grief, his mental health, his
employment, and his public disputes. That makes the offline default a data
protection property and not only an engineering one.
"""

from .load import (
    AUTHOR,
    CORPUS_ROOT,
    MACHINE_TYPES,
    TESTIMONY_TYPES,
    CorpusDocument,
    corpus_stats,
    load_corpus,
)

__all__ = [
    "load_corpus",
    "corpus_stats",
    "CorpusDocument",
    "CORPUS_ROOT",
    "AUTHOR",
    "TESTIMONY_TYPES",
    "MACHINE_TYPES",
]
