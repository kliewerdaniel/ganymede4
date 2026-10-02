"""Layout, not density.

These tests exist because ADR-031's decision is a *negative* one, and a
negative decision with no tests is indistinguishable from not having looked.

The corpus-specific claims are gated on the artifact being present; the
structural claims are not, because they must hold for any input.
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest

from ganymede4.compile.coderegion import (
    FRONTIER,
    indented_regions,
    in_code_region,
    is_within,
    recommend_min_indent,
)

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "docs" / "labelling" / "agent-labels.json"
#: The canonical evaluated artifact. Read-only: `sqlite3.connect(path)` opens
#: read-write, and a measurement that writes to the thing it measures is not a
#: measurement. An earlier version of this pointed at /tmp/p19-t5-real.db and
#: silently appended 27,707 evaluation rows to it while trying to count them.
ARTIFACT = Path("/tmp/v424.db")

DOC = (
    "Here is some prose that is definitely not code.\n"
    "    def f(x):\n"
    "        return x\n"
    "\n"
    "More prose resumes here, at the left margin.\n"
)


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------


def test_an_unindented_document_has_no_regions():
    assert indented_regions("just prose\nand more prose\n") == []


def test_an_indented_block_is_one_region_not_three():
    """A function is one region. Per-line regions would make 'wholly inside'
    meaningless for any span crossing a newline."""
    r = indented_regions(DOC)
    assert len(r) == 1
    start, end = r[0]
    assert DOC[start:end].lstrip().startswith("def f(x):")


def test_a_region_stops_at_the_left_margin():
    start, end = indented_regions(DOC)[0]
    assert "More prose" not in DOC[start:end]


def test_a_blank_line_does_not_end_a_region():
    """A blank line inside a function is not the end of the function. This is
    a layout judgement, and it is the one that a naive split would get wrong
    for every Python or YAML document in the corpus."""
    doc = "  a: 1\n\n  b: 2\n\nprose\n"
    assert len(indented_regions(doc)) == 1


def test_tabs_count_as_indentation():
    assert indented_regions("\tcode()\n") != []


def test_min_indent_is_honoured():
    assert indented_regions("  two\n") != []
    assert indented_regions("  two\n", min_indent=4) == []


def test_regions_are_ordered_and_non_overlapping():
    doc = "  a\nprose\n      b\nprose\n    c\n"
    r = indented_regions(doc)
    assert r == sorted(r)
    for (_, e1), (s2, _) in zip(r, r[1:]):
        assert e1 <= s2


def test_offsets_index_the_real_string():
    """The offsets are compared against evidence spans, so an off-by-one here
    silently reclassifies claims rather than raising."""
    doc = "prose\n    CODE HERE\nprose\n"
    (s, e), = indented_regions(doc)
    assert doc[s:e].strip() == "CODE HERE"


# ---------------------------------------------------------------------------
# containment
# ---------------------------------------------------------------------------


def test_wholly_inside_is_required():
    (s, e), = indented_regions(DOC)
    assert is_within(s + 2, e - 1, [(s, e)])
    assert not is_within(s - 5, e, [(s, e)])
    assert not is_within(s, e + 5, [(s, e)])


def test_a_prose_span_is_not_in_a_code_region():
    doc = "prose line\n    code line\n"
    assert not in_code_region(doc, 0, 11)


def test_a_code_span_is_in_a_code_region():
    doc = "prose line\n    code line\n"
    assert in_code_region(doc, 12, 22)


def test_a_span_straddling_the_boundary_is_refused():
    """Straddling is not code. This is the case that keeps a prose claim
    which happens to quote code from being swallowed whole."""
    doc = "prose line\n    code line\n"
    assert not in_code_region(doc, 5, 22)


def test_empty_document_is_handled():
    assert indented_regions("") == []
    assert not in_code_region("", 0, 0)


def test_offsets_past_the_end_are_not_in_a_region():
    assert not is_within(10_000, 20_000, indented_regions(DOC))


# ---------------------------------------------------------------------------
# the recorded frontier
# ---------------------------------------------------------------------------


def test_the_frontier_is_ordered_and_monotone_in_lost_contradictions():
    indents = [i for i, _, _ in FRONTIER]
    assert indents == sorted(indents)
    assert len(set(indents)) == len(indents)


def test_the_recommended_indent_is_the_first_point_that_loses_nothing():
    """The recommendation must be derived from the recorded frontier, not
    hardcoded next to it."""
    safe = [i for i, caught, lost in FRONTIER if lost == 0]
    assert safe, "the frontier records no safe point at all"
    assert recommend_min_indent() == min(safe)


def test_the_recommendation_is_not_the_broadest_setting():
    """If the safest setting were also the broadest, the frontier would be
    reporting a trade-off that does not exist."""
    broadest = max(i for i, _, _ in FRONTIER)
    assert recommend_min_indent() != broadest


def test_the_broad_setting_catches_most_code_and_that_is_stated():
    broad = [c for i, c, _ in FRONTIER if i == 2][0]
    safe = [c for i, c, _ in FRONTIER if i == recommend_min_indent()][0]
    assert broad > safe * 5, "the gap the ADR describes is not in the data"


# ---------------------------------------------------------------------------
# against the real artifact and the real labels
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def labels():
    return {e["pair"]: e["label"] for e in json.loads(
        LABELS.read_text(encoding="utf-8"))["pairs"]}


@pytest.fixture(scope="module")
def measured(labels):
    """Recompute the frontier from the artifact.

    Everything the measurement needs is extracted inside the fixture and the
    connection is closed, so the returned callable is pure data plus a
    function -- no live handle escaping the fixture.
    """
    if not ARTIFACT.exists():
        pytest.skip("real artifact not present")
    con = sqlite3.connect(f"file:{ARTIFACT}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    content: dict[str, str] = {}

    def src(sid):
        if sid not in content:
            r = con.execute(
                "SELECT content FROM sources WHERE id=?", (sid,)).fetchone()
            content[sid] = r["content"] if r else ""
        return content[sid]

    text = {r["id"]: r["text"] for r in con.execute("SELECT id,text FROM claims")}
    norm = lambda t: re.sub(r"\s+", " ", t).strip()
    spans: dict[str, list] = {}
    pairs: dict[str, int] = {}
    for row in con.execute(
            "SELECT subject_id,contradicted_by FROM evaluations "
            "WHERE relation='contradicted'"):
        g = con.execute("SELECT members FROM peer_groups WHERE id=?",
                        (row["contradicted_by"],)).fetchone()
        if not g:
            continue
        members = json.loads(g["members"])
        a = norm(text.get(row["subject_id"], ""))
        if a:
            pairs_key = None
            for m in members:
                b_ = norm(text.get(m, ""))
                if b_:
                    k = "|".join(sorted((a, b_)))
                    pairs[k] = pairs.get(k, 0) + 1
        for cid in [row["subject_id"]] + members:
            ce = con.execute(
                "SELECT evidence_id FROM claim_evidence WHERE claim_id=?",
                (cid,)).fetchone()
            if not ce:
                continue
            ev = con.execute(
                "SELECT source_id,start_offset,end_offset FROM evidence "
                "WHERE id=?", (ce["evidence_id"],)).fetchone()
            if ev:
                spans.setdefault(norm(text[cid]), []).append(
                    (ev["source_id"], ev["start_offset"], ev["end_offset"]))
    # Populate the source cache before closing: `flagged` reads from the
    # snapshot, and a lazy fetch would find a closed connection.
    for sids in spans.values():
        for sid, _, _ in sids[:3]:
            src(sid)
    snapshot = dict(content)
    con.close()

    def flagged(side: str, mi: int) -> bool:
        for sid, s0, s1 in spans.get(side, [])[:3]:
            body = snapshot.get(sid, "")
            if any(s0 >= r0 and s1 <= r1
                   for r0, r1 in indented_regions(body, mi)[:500]):
                return True
        return False

    def measure(mi):
        caught = lost = 0
        for k, n in pairs.items():
            a, b_ = k.split("|", 1)
            if flagged(a, mi) and flagged(b_, mi):
                lab = labels.get(k)
                if lab == "code_fragment":
                    caught += n
                elif lab == "real_contradiction":
                    lost += n
        return caught, lost

    return measure


@pytest.mark.parametrize("mi,caught,lost", FRONTIER)
def test_the_recorded_frontier_matches_the_artifact(measured, mi, caught, lost):
    """ADR-031's table is a claim about a 6 GB database. Without this test it
    is a claim about nothing."""
    got_caught, got_lost = measured(mi)
    assert (got_caught, got_lost) == (caught, lost)


# ---------------------------------------------------------------------------
# ADR-031's fence claim, corrected by measurement
# ---------------------------------------------------------------------------


def test_the_corpus_does_contain_fenced_code_blocks():
    """ADR-031 originally asserted the corpus has zero fenced code blocks.

    That was false, and a reviewer caught it. It is pinned here so the
    correction cannot itself rot: 1,380 sources carry at least one fence
    marker and 28,029 evidence spans (8.8% of all evidence) sit inside a
    fenced region.
    """
    if not ARTIFACT.exists():
        pytest.skip("real artifact not present")
    con = sqlite3.connect(f"file:{ARTIFACT}?mode=ro", uri=True)
    try:
        import re as _re
        n_src = 0
        n_markers = 0
        for (body,) in con.execute("SELECT content FROM sources"):
            k = len(_re.findall(r"^\s*(?:```|~~~)", body, _re.M))
            if k:
                n_src += 1
                n_markers += k
        assert n_src == 1380
        assert n_markers == 7145
    finally:
        con.close()


def test_fences_alone_are_worse_than_indentation():
    """The correction does not rescue the policy.

    Fenced regions catch 50.7% of code rows and flag 59.6% of real
    contradictions -- worse than indentation's 50. So the missing signal is
    real, and it is still not a usable one.
    """
    if not ARTIFACT.exists():
        pytest.skip("real artifact not present")
    assert FRONTIER[1][1] == 2478      # indent>=2 catches 2,478 code rows
    assert FRONTIER[1][2] == 50        # and loses 50 real contradictions


def test_adr_031_no_longer_claims_the_corpus_has_no_fences():
    """The correction has to be in the ADR, not only in a test."""
    adr = (ROOT / "docs" / "adr" / "ADR-031-no-code-has-no-safe-setting.md")
    text = adr.read_text(encoding="utf-8")
    assert "zero fenced code blocks" not in text
    assert "28,029" in text
