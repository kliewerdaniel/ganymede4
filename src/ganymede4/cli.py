"""ganymede4 command line: inspect an artifact, and change nothing.

Task 5 required resolving `[project.scripts] ganymede4 = "ganymede4.cli:main"`,
which pointed at a module that did not exist. Two honest options were
available: delete the entry, or build it. It is built, because a project whose
thesis is that derived artifacts should be inspectable by anyone benefits from
a console that works -- and because deleting the entry would have left
`pip install` succeeding while the command failed, which is the worse failure
mode (it moves the breakage to a user's machine and off this one).

Four subcommands, all read-only:

    build    compile + evaluate + reseal + audit, one command (ADR-025)
    audit    run the independent auditor, print its report
    query    ask the witness a question; print the answer and its boundary
    verify   print the artifact's identity and check it against its contents

**Every subcommand here is read-only except `build`, which writes a new
database and never touches an existing one without `--force`.** That is a
design constraint, not an oversight. A console with a write path is a
governance hole: it would be a second, undocumented way to change epistemic
state that bypasses the policy gateway this project spent five phases
building. If you want to change beliefs, the path is the gateway, and the
gateway is not reachable from here on purpose. `verify` exists precisely
because the useful question after a build is "did this artifact keep its
identity", and answering that must not require writing to it.

The auditor is invoked as a subprocess, never imported (ADR-014): an auditor
that imports the code it audits cannot find that code's bugs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

__all__ = ["main", "build_parser"]

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_SCRIPTS = _REPO / "scripts"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ganymede4",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "build",
        help="compile + evaluate + reseal + audit in one command",
        description="Build the corpus artifact end to end. Writes a new database.",
    )
    p.add_argument("--db", default="build/ganymede4-corpus.db")
    p.add_argument("--corpus", default=None, help="corpus root directory")
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-evaluate", action="store_true")
    p.add_argument("--force", action="store_true", help="overwrite an existing --db")

    p = sub.add_parser("audit", help="run the independent auditor on an artifact")
    p.add_argument("db", help="path to the artifact database")
    p.add_argument("--json", action="store_true", help="print the raw report only")

    p = sub.add_parser("query", help="ask the witness a question about an artifact")
    p.add_argument("db", help="path to the artifact database")
    p.add_argument("question", help="what to ask, e.g. 'is the model the intelligence?'")
    p.add_argument("--limit", type=int, default=5, help="max claims to show")
    p.add_argument("--boundary", action="store_true", help="print the witness's search boundary")

    p = sub.add_parser(
        "verify",
        help="print an artifact's identity and check it against its contents",
    )
    p.add_argument("db", help="path to the artifact database")
    p.add_argument("--audit", action="store_true", help="also run the independent auditor")

    return parser


# ------------------------------------------------------------------ build
def _cmd_build(args: argparse.Namespace) -> int:
    """Delegate to the build script rather than reimplementing it.

    Delegating matters: if the CLI grew its own compile path, then "what does
    the console build" and "what does the script build" would be two questions
    with two answers, and the project's own rule is that a console which
    disagrees with the pipeline is a defect, not a convenience.
    """
    script = _SCRIPTS / "build_artifact.py"
    if not script.exists():  # pragma: no cover - only in a broken install
        print(f"cannot find {script}", file=sys.stderr)
        return 2

    argv = [
        sys.executable, str(script),
        "--db", args.db,
        "--python", args.python,
    ]
    if args.corpus:
        argv += ["--corpus", args.corpus]
    if args.limit is not None:
        argv += ["--limit", str(args.limit)]
    if args.no_evaluate:
        argv += ["--no-evaluate"]
    if args.force:
        # build_artifact.py overwrites by default and refuses under
        # --keep-existing, so --force is expressed as its absence.
        pass
    else:
        argv += ["--keep-existing"]

    return subprocess.run(argv, cwd=str(_REPO)).returncode


# ------------------------------------------------------------------ audit
def _audit(db: str, python: str | None = None) -> tuple[int, dict]:
    script = _SCRIPTS / "audit_provenance.py"
    result = subprocess.run(
        [python or sys.executable, str(script), db],
        capture_output=True,
        text=True,
    )
    try:
        return result.returncode, json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stdout[-2000:], file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        return result.returncode or 2, {}


def _cmd_audit(args: argparse.Namespace) -> int:
    code, report = _audit(args.db)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return code
    if not report:
        return code or 2
    print(f"independent audit of {args.db}")
    print(f"  (run as a separate process; it does not import ganymede4)\n")
    for key in sorted(report):
        value = report[key]
        if isinstance(value, list):
            shown = "[]" if not value else f"{len(value)} item(s)  <-- DEFECT"
        else:
            shown = str(value)
        print(f"  {key:32} {shown}")
    print(f"\n  clean: {report.get('clean')}")
    return code


# ------------------------------------------------------------------ query
def _cmd_query(args: argparse.Namespace) -> int:
    from ganymede4.compile import rebuild_manifest
    from ganymede4.knowledge.store import Store
    from ganymede4.witness.witness import Witness

    path = Path(args.db)
    if not path.exists():
        print(f"no artifact at {path}", file=sys.stderr)
        print("  build one with:  ganymede4 build --db", path, file=sys.stderr)
        return 2

    with Store(str(path)) as store:
        try:
            manifest = rebuild_manifest(store)
        except Exception as exc:  # noqa: BLE001
            print(f"cannot reconstruct this artifact's manifest: {exc}", file=sys.stderr)
            print("  was it built by this pipeline?  ganymede4 verify", path, file=sys.stderr)
            return 1
        witness = Witness(store, manifest)
        answer = witness.ask(args.question, limit=args.limit)

        print(f"question: {args.question}")
        print(f"state   : {answer.state.value}")
        print(f"claims  : {len(answer.claims)}")
        if answer.is_absence:
            print("absence : the artifact was searched and no answer was found")
            print("          (this is not the same as 'the answer is no')")
            if answer.investigation_id:
                print(f"          investigation: {answer.investigation_id}")
        # Claims and their evidence are separate tuples with no link field
        # between them, so the citation list is shown alongside rather than
        # interleaved. Attributing an evidence span to the wrong claim would
        # be worse than not pairing them at all, so this does not guess.
        print("\nclaims:")
        for claim_id, text in answer.claims:
            print(f"\n  - {text[:300]}")
            print(f"    {claim_id}")
        if answer.evidence:
            print(f"\nevidence ({len(answer.evidence)} span(s)):")
            for ev in answer.evidence[: args.limit * 3]:
                print(f"  {ev.citation()}")
            if len(answer.evidence) > args.limit * 3:
                print(f"  ... and {len(answer.evidence) - args.limit * 3} more")

        if args.boundary:
            b = witness.boundary()
            print("\nsearch boundary (this is the whole of what the witness knows):")
            print(f"  version: {b.version}")
            print(f"  root   : {b.root}")
            print(f"  method : {b.method}")
            print(f"  sources: {', '.join(b.sources)}")
            print(f"  state counts: {json.dumps(b.state_counts, sort_keys=True)}")
            print(f"  index: {json.dumps(b.index, sort_keys=True)}")
            if b.answered_with_absence:
                print("  questions that returned an honest absence:")
                for q in b.answered_with_absence:
                    print(f"    - {q}")
    return 0


# ----------------------------------------------------------------- verify
def _cmd_verify(args: argparse.Namespace) -> int:
    """Print the recorded identity, then check it against the contents.

    The interesting half is the second part. A recorded digest is just a
    string someone wrote down; it means nothing unless something recomputes
    it. That is the whole reason the auditor is a separate process.
    """
    from ganymede4.compile import manifest_matches_recorded
    from ganymede4.knowledge.store import Store

    path = Path(args.db)
    if not path.exists():
        print(f"no artifact at {path}", file=sys.stderr)
        return 2

    with Store(str(path)) as store:
        held = store.recorded_artifact()
        if not held:
            print("this database has no recorded artifact", file=sys.stderr)
            return 1
        counts = dict(store.counts())
        print(f"artifact: {path}")
        print(f"  version      {held.get('version')}")
        print(f"  root         {held.get('root')}")
        print(f"  state_epoch  {held.get('state_epoch')}")
        print(f"  state_digest {held.get('state_digest')}")
        print(f"  counts       {json.dumps(counts, sort_keys=True)}")

        # Recompute the identity from the rows on disk rather than printing the
        # recorded one. Printing a stored digest verifies nothing: it is a
        # string someone wrote down. This is the difference between "here is
        # what it claims to be" and "here it is".
        matches, rebuilt, recorded = manifest_matches_recorded(store)
        print(f"\n  recomputed root  {rebuilt.root}")
        if not matches:
            print("  MISMATCH: the rows on disk do not produce the recorded root.")
            print(f"    recorded: {recorded.get('root') if recorded else '(none)'}")
            print("    this artifact was modified outside the pipeline")
            return 1
        print("  MATCH: the rows on disk reproduce the recorded root")

    if args.audit:
        code, report = _audit(args.db)
        print(f"\n  independent audit clean: {report.get('clean')}")
        return code
    print("\n(pass --audit to verify the digest against the contents)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "build": _cmd_build,
        "audit": _cmd_audit,
        "query": _cmd_query,
        "verify": _cmd_verify,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
