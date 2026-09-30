#!/usr/bin/env python3
"""Build the corpus artifact end to end, in one command.

compile -> evaluate -> reseal -> independent audit -> print the identity.

The point of this script is that a person who has never seen the repository
should be able to produce the artifact and *verify* it without reading an
ADR to learn which of the four steps is allowed to be skipped. The earlier
story was spread across `compile_corpus.py` (which does compile and
evaluation), `audit_provenance.py` (which is a separate process on purpose),
and commit messages.

It fails closed. A build that compiles but does not audit clean has not
produced an artifact; it has produced a claim about one. So a non-zero audit
is a non-zero exit, and the printed block says which step failed.

The auditor is invoked as a *subprocess* rather than imported. That is
deliberate and load-bearing (ADR-014): an auditor that imports the code it
audits cannot catch that code's bugs, and here it is the only component
trusted to be adversarial. Importing it for convenience would quietly remove
the property that makes it worth having.

Paths are arguments. The corpus lives outside the repository and its location
differs per machine, so nothing here is hardcoded to a particular home
directory; the defaults are the ones this repository was developed against and
`GANYMEDE4_CORPUS_ROOT` overrides the corpus side.

What it does NOT do, because the artifact is not a test fixture:

- It does not assert the corpus has a particular shape. A shape change in
  someone's own record is information, not a failure. Only the substrate's
  invariants can fail this script.
- It does not upload, publish, or commit anything.
- It does not compare against a recorded root. A hardcoded expected hash
  would be a corpus-fitted constant wearing a test's clothes; the identity
  check that matters is that reversing the input order reproduces the *same*
  root on *this* run, which the compile step already performs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from ganymede4.compile.compiler import SourceSpec, compile_corpus  # noqa: E402
from ganymede4.knowledge.evaluator import Evaluator  # noqa: E402
from ganymede4.knowledge.store import Store  # noqa: E402
from ganymede4.witness.witness import Witness  # noqa: E402

#: Fixed so that recompiling the same bytes produces the same artifact.
#: A wall-clock "now" here would make the Merkle root a function of when
#: anyone happened to run it, which would destroy the reproducibility claim
#: this whole exercise exists to demonstrate.
TRANSACTION_TIME = "2026-09-28T00:00:00Z"

RULE = "=" * 72


def _step(number: int, title: str) -> None:
    print(f"\n{RULE}\n  step {number}/4  {title}\n{RULE}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--db",
        default="build/ganymede4-corpus.db",
        help="artifact path to write (default: build/ganymede4-corpus.db)",
    )
    parser.add_argument(
        "--corpus",
        default=None,
        help=(
            "corpus root directory "
            "(default: $GANYMEDE4_CORPUS_ROOT, else ~/Projects/Chris)"
        ),
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="interpreter used to run the auditor (default: this one)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="only the first N documents (a smoke build, not the artifact)",
    )
    parser.add_argument(
        "--no-evaluate",
        action="store_true",
        help="compile only; the artifact will hold no verdicts",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="fail instead of overwriting an existing --db",
    )
    args = parser.parse_args(argv)

    # Resolved before the corpus package is imported: load.py binds
    # CORPUS_ROOT at import time, so a --corpus flag honoured after that
    # import would be silently ignored -- the worst kind of flag.
    if args.corpus is not None:
        os.environ["GANYMEDE4_CORPUS_ROOT"] = str(Path(args.corpus).expanduser())

    from ganymede4.corpus import CORPUS_ROOT, corpus_stats, load_corpus  # noqa: E402

    print(f"corpus root: {CORPUS_ROOT}")
    if not CORPUS_ROOT.exists():
        print(f"FAILED: no corpus at {CORPUS_ROOT}", file=sys.stderr)
        print("  pass --corpus PATH or set GANYMEDE4_CORPUS_ROOT.", file=sys.stderr)
        return 2

    db = Path(args.db).expanduser()
    if db.exists():
        if args.keep_existing:
            print(f"refusing to overwrite {db} (--keep-existing)", file=sys.stderr)
            return 2
        db.unlink()
    db.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()

    # ------------------------------------------------------------------ 1
    _step(1, "compile the corpus into claims")
    try:
        stats = corpus_stats(limit=args.limit)
    except Exception as exc:  # noqa: BLE001 - report, do not traceback
        print(f"FAILED: cannot read the corpus: {exc}", file=sys.stderr)
        return 2

    print(f"  documents   {stats['documents']:,}")
    print(f"  bytes       {stats['bytes'] / 1e6:.1f} MB")
    print(f"  machine     {stats['machine_documents']:,} "
          f"({100 * stats['machine_documents'] / max(stats['documents'], 1):.0f}% "
          f"of this corpus is a model's prior output, never testimony)")
    print(f"  digest      {stats['corpus_digest'][:32]}...")
    if args.limit is not None:
        print(f"  NOTE: --limit {args.limit} truncates after Reddit, so the machine")
        print("        share above describes this sample, not the full corpus.")

    sources = [
        SourceSpec(doc.uri, doc.content, source_type=doc.source_type, meta=doc.meta)
        for doc in load_corpus(limit=args.limit)
    ]

    compile_started = time.monotonic()
    with Store(str(db)) as store:
        artifact = compile_corpus(store, sources, transaction_time=TRANSACTION_TIME)
        print(f"\n  compiled in {time.monotonic() - compile_started:.1f}s")
        counts = dict(store.counts())
        compile_root = artifact.manifest.root
        compile_version = artifact.manifest.version
        print(f"  version     {compile_version}")
        print(f"  root        {compile_root}")
        print(f"  sources     {counts.get('sources', 0):,}")
        print(f"  claims      {counts.get('claims', 0):,}")
        print(f"  evidence    {counts.get('evidence', 0):,}")
        print(f"  heuristics  {len(artifact.heuristic_ids):,}")
        print(f"  dropped     {artifact.dropped_segments:,} non-propositions")

        # Reproducibility, checked here because this is the only place the
        # source list and the compiled artifact coexist.
        same = (
            compile_corpus(
                store, list(reversed(sources)), transaction_time=TRANSACTION_TIME
            ).manifest.root
            == compile_root
        )
        print(f"  reversed-order recompile identical: {same}")
        if not same:
            print("  FAILED: artifact identity depends on iteration order", file=sys.stderr)
            return 1

        witness = Witness(store, artifact.manifest)
        print("\n  witness (a read-only view; it may not change anything):")
        for question in ("is the model the intelligence?", "provenance"):
            answer = witness.ask(question)
            print(f"    {question[:30]:32} {answer.state.value:12} "
                  f"{len(answer.claims)} claim(s)")

        # --------------------------------------------------------------- 2
        if not args.no_evaluate:
            _step(2, "evaluate (deterministic relations only)")
            Evaluator(store, artifact.manifest).evaluate_all()  # re-seals (ADR-021)
            by_state: dict[str, int] = {}
            for row in store.db.execute("SELECT state, COUNT(*) n FROM claims GROUP BY state"):
                by_state[row["state"]] = row["n"]
            for state, count in sorted(by_state.items(), key=lambda kv: -kv[1]):
                print(f"  {state:16} {count:>9,}")
            held = store.recorded_artifact() or {}
            print(f"  sealed as   {held.get('version', '?')}")
        else:
            _step(2, "evaluate  [skipped: --no-evaluate]")
            print("  this artifact contains NO verdicts. It is not a finished build.")
            held = store.recorded_artifact() or {}

    # ------------------------------------------------------------------ 3
    _step(3, "reseal (flush any pending state seal)")
    # compile/evaluate both seal in their own funnel (ADR-021, ADR-022), so this
    # is a no-op on a clean path and a repair on a path that left a pending
    # seal. It is here so a reader can see that the artifact's recorded digest
    # is flushed *before* it is audited, not hoped about.
    with Store(str(db)) as store:
        store.reseal()
        held = store.recorded_artifact() or {}
        print(f"  version     {held.get('version', '?')}")
        print(f"  root        {held.get('root', '?')}")
        print(f"  state_epoch {held.get('state_epoch', '?')}")
        print(f"  digest      {held.get('state_digest', '?')}")

    # ------------------------------------------------------------------ 4
    _step(4, "independent audit")
    # Subprocess on purpose. See the module docstring: an auditor that imports
    # its subject cannot find its subject's bugs.
    auditor = HERE / "audit_provenance.py"
    result = subprocess.run(
        [args.python, str(auditor), str(db)],
        capture_output=True,
        text=True,
    )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("FAILED: auditor produced no JSON report", file=sys.stderr)
        print(result.stdout[-2000:], file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        return 1

    for key in sorted(report):
        value = report[key]
        if isinstance(value, list):
            shown = "[]" if not value else f"{len(value)} item(s)"
        else:
            shown = str(value)
        print(f"  {key:32} {shown}")

    # ---------------------------------------------------------------- done
    clean = report.get("clean") is True
    print(f"\n{RULE}")
    if not clean:
        print("  BUILD FAILED — the artifact compiled but does not audit clean.")
        print("  A compile that does not audit clean is not a partial success;")
        print("  it is an artifact you should not use.")
        print(RULE)
        return 1

    print("  BUILD OK — the artifact audits clean against an auditor that")
    print("  does not import the code it is auditing.")
    print(f"  artifact: {db}")
    print(f"  version : {report.get('artifact_recorded') and held.get('version', '?')}")
    print(f"  in {time.monotonic() - started:.1f}s total")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
