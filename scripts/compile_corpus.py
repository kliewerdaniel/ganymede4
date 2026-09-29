#!/usr/bin/env python3
"""Compile the real corpus and report what came out (ADR-010).

The tests use small deterministic fixtures so the suite stays fast and
hermetic. This script is where the actual 18,930-document record meets the
substrate — the place a claim about "it works on real data" either holds or
does not.

It reports rather than asserts. The corpus is the author's own record and its
contents are not a test fixture, so a shape change in it is information, not a
failure. The only things this exits non-zero on are the substrate's own
invariants: a broken chain, an unclean audit, a compile that does not
reproduce.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sovereign_runtime.compile.compiler import SourceSpec, compile_corpus  # noqa: E402
from sovereign_runtime.corpus import MACHINE_TYPES, corpus_stats, load_corpus  # noqa: E402
from sovereign_runtime.knowledge.evaluator import Evaluator  # noqa: E402
from sovereign_runtime.knowledge.store import Store  # noqa: E402
from sovereign_runtime.witness.witness import Witness  # noqa: E402

TRANSACTION_TIME = "2026-09-28T00:00:00Z"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/tmp/ganymede4-corpus.db")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--no-evaluate",
        action="store_true",
        help="skip the evaluation pass; still useful when you only want the artifact",
    )
    args = parser.parse_args()

    print("=" * 68)
    print("ganymede4 — compile the real corpus")
    print("=" * 68)

    stats = corpus_stats(limit=args.limit)
    print(f"\ncorpus: {stats['documents']:,} documents, {stats['bytes'] / 1e6:.1f} MB")
    for source_type, count in stats["by_type"].items():
        if source_type in MACHINE_TYPES:
            tag = "machine"
        elif source_type.endswith("_other"):
            # Not testimony and not the author's own record — someone else's
            # words that appear in the export as quoted context. Labelling
            # this "testimony" is precisely the error ADR-010 exists to
            # prevent, so it gets its own line.
            tag = "third-party"
        else:
            tag = "testimony"
        print(f"  {source_type:26} {count:>7,}  ({tag})")
    machine_pct = 100 * stats["machine_documents"] / max(stats["documents"], 1)
    print(f"\n  -> {machine_pct:.0f}% of this corpus is a model's prior output.")
    if args.limit is not None:
        # The loader emits Reddit first, so a truncated run is almost entirely
        # testimony. Saying "2% machine output" for a --limit run would be a
        # true number describing a misleading sample.
        print(f"  NOTE: --limit {args.limit} truncates after Reddit; the machine")
        print("        share above describes this sample, not the full corpus.")
    print(f"  corpus digest: {stats['corpus_digest'][:32]}...")

    db = Path(args.db)
    if db.exists():
        db.unlink()

    sources = [
        SourceSpec(doc.uri, doc.content, source_type=doc.source_type, meta=doc.meta)
        for doc in load_corpus(limit=args.limit)
    ]
    print(f"\ncompiling {len(sources):,} sources...")

    started = time.monotonic()
    with Store(str(db)) as store:
        artifact = compile_corpus(store, sources, transaction_time=TRANSACTION_TIME)
        elapsed = time.monotonic() - started

        counts = dict(store.counts())
        print(f"\ncompiled in {elapsed:.1f}s")
        print(f"  version : {artifact.manifest.version}")
        print(f"  root    : {artifact.manifest.root[:32]}...")
        print(f"  sources : {counts.get('sources', 0):,}")
        print(f"  claims  : {counts.get('claims', 0):,}")
        print(f"  evidence: {counts.get('evidence', 0):,}")
        print(f"  heurist.: {counts.get('claims', 0) and len(artifact.heuristic_ids):,}")

        # Reproducibility: the same bytes must give the same root. A loader
        # that produced a fresh digest per run would break this, and the whole
        # artifact-identity story depends on it.
        recompiled = compile_corpus(
            store, list(reversed(sources)), transaction_time=TRANSACTION_TIME
        )
        same = recompiled.manifest.root == artifact.manifest.root
        print(f"\n  reversed-order recompile identical: {same}")
        if not same:
            print("  FAIL: artifact identity is a function of iteration order")
            return 1

        witness = Witness(store, artifact.manifest)
        print("\nasking the witness:")
        for question in (
            "is the model the intelligence?",
            "provenance",
            "LocalLLaMA",
        ):
            answer = witness.ask(question)
            print(
                f"  {question[:34]:36} {answer.state.value:14} "
                f"{len(answer.claims)} claim(s)"
            )
            for claim_id, text in answer.claims[:1]:
                print(f"      {text[:88]}")

        if not args.no_evaluate:
            print("\nevaluating (deterministic relations only):")
            evaluator = Evaluator(store, artifact.manifest)
            evaluator.evaluate_all()
            counts = dict(store.counts())
            by_state: dict[str, int] = {}
            for row in store.db.execute("SELECT state, COUNT(*) n FROM claims GROUP BY state"):
                by_state[row["state"]] = row["n"]
            for state, count in sorted(by_state.items(), key=lambda kv: -kv[1]):
                print(f"  {state:16} {count:>7,}")
            print(f"  contradictions found: {len(evaluator.contradictions())}")

    print("\nrun the independent auditor:")
    print(f"  {Path(__file__).parent / 'audit_provenance.py'} {db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
