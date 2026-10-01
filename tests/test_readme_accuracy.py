"""The README is the interface, so the README gets tested.

Every claim in this file was true when written and false three ADRs later.
The repo had 500 tests when the README said 500; by the time a reviewer
counted, it was 653, and the README still advertised 500 while also denying
that a real model had ever run — which had happened in ADR-028.

Documentation drift is cheap to fix and expensive to catch late, because a
stale README is what a reader trusts. So the load-bearing numbers are
pinned here against the suite that actually runs.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PHASES = ROOT / "docs" / "status" / "PHASES.md"


@pytest.fixture(scope="module")
def readme():
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def collected():
    """The real test count, summed from per-file collection counts.

    Three earlier versions of this fixture were wrong and two of them
    *passed*:

    - reading `tests="N"` from a `--collect-only` junit xml, which writes
      `tests="0"`;
    - asserting `f"{collected} tests" in readme` with `collected == 0`,
      which passed because the README happened to contain "0 tests";
    - running the full suite from inside a test, which nests pytest inside
      pytest and hangs.

    This form sums `tests/<file>.py: N` lines. It is cheap, needs no nested
    run, and cannot silently become zero.
    """
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    counts = [int(m) for m in re.findall(r"^tests/\S+\.py: (\d+)$",
                                        out.stdout, re.M)]
    assert counts, f"no per-file counts parsed:\n{out.stdout[-400:]}"
    total = sum(counts)
    assert total > 100, f"implausible collection count: {total}"
    return total


def test_the_readme_states_the_real_test_count(readme, collected):
    """The number a reader runs first. If it is wrong, everything else in the
    README is discounted."""
    assert re.search(rf"\b{collected} tests\b", readme), (
        f"the suite runs {collected} tests but the README does not say so"
    )
    assert not re.search(r"\b0 tests\b", readme)


def test_the_readme_does_not_advertise_a_stale_count(readme, collected):
    """Belt and braces: no other plausible count should be lying around."""
    for stale in ("500 tests", "499 pass", "614 passed", "630 passed"):
        assert stale not in readme, f"README still claims {stale!r}"


def test_the_readme_does_not_deny_the_model_run(readme):
    """ADR-028 ran a real qwen3:4b model. The README said it never had.

    The specific failure this guards: a *denial* of a capability that
    exists is worse than a stale number, because a reader concludes the
    system is untested where it is in fact tested adversarially.
    """
    assert "never been run with a real model" not in readme
    assert "qwen3:4b" in readme


def test_the_readme_names_the_current_phase(readme):
    assert re.search(r"\*\*Phase 2\d\b", readme), "no current phase stated"


def test_the_phases_document_has_an_entry_for_the_current_phase():
    text = PHASES.read_text(encoding="utf-8")
    assert "## Phase 20" in text, "PHASES.md has no Phase 20 entry"


def test_every_recent_adr_appears_in_the_phase_20_table():
    """A record that is not in the phase history is a record nobody finds.

    Checked against the Phase 20 index table specifically: ADR-031 is cited
    three times elsewhere in the file, so a document-wide containment check
    survived deleting its table row.
    """
    text = PHASES.read_text(encoding="utf-8")
    i = text.index("## Phase 20")
    j = text.index("### The model is not an input to the write", i)
    table = text[i:j]
    for n in range(27, 32):
        assert f"ADR-0{n}" in table, f"ADR-0{n} is missing from the Phase 20 table"


def test_the_readme_makes_no_absolute_claims_about_other_corpora(readme):
    """Single-corpus risk, stated as a limit rather than as a result."""
    low = readme.lower()
    assert "this corpus" in low or "measurements of *this* corpus" in low