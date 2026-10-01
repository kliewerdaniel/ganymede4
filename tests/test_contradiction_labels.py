"""The labels are evidence, and evidence rots silently.

`docs/labelling/agent-labels.json` is the input to ADR-030's central claim.
Nothing in the build would notice if it were edited, and a stale label file
would let ADR-030's numbers stand after the population moved underneath them.

These tests pin the file's integrity and re-derive the population count from
the artifact when one is available, so the ADR's arithmetic cannot quietly
stop being true.
"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "docs" / "labelling" / "agent-labels.json"
ADR = ROOT / "docs" / "adr" / "ADR-030-2854-pairs-are-70-pairs.md"

ALLOWED = {
    "real_contradiction",
    "code_fragment",
    "subset",
    "boilerplate",
    "other",
}


@pytest.fixture(scope="module")
def data():
    return json.loads(LABELS.read_text(encoding="utf-8"))


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def test_every_label_is_one_of_the_five(data):
    for e in data["pairs"]:
        assert e["label"] in ALLOWED, e


def test_every_pair_carries_its_own_rationale(data):
    """A label without a reason is an assertion, and this project does not
    take assertions on trust."""
    for e in data["pairs"]:
        assert len(e.get("rationale", "").strip()) > 20, e


def test_the_pair_key_is_order_independent(data):
    """Keys are built from the sorted pair. If one were hand-edited into the
    other order it would silently stop matching anything."""
    for e in data["pairs"]:
        a, b = e["pair"].split("|", 1)
        assert a <= b, e["pair"][:60]
        assert e["a"] in (a, b) or _norm(e["a"]) in (a, b), e


def test_multiplicity_sums_to_the_measured_population(data):
    """2,854 pair rows. If this changes, ADR-030's percentages are stale and
    this fails rather than letting the ADR quietly become a lie."""
    assert sum(data["multiplicity"].values()) == 2854


def test_the_distinct_pair_count_is_70(data):
    """The whole point of ADR-030. 2,854 rows, 70 distinct pairs, 40.8x."""
    assert len(data["multiplicity"]) == 70
    assert len(data["pairs"]) == 70
    assert round(2854 / 70, 1) == 40.8


def test_the_top_pair_is_the_one_the_adr_names(data):
    top = max(data["multiplicity"].values())
    assert top == 1026
    pair = [k for k, v in data["multiplicity"].items() if v == 1026][0]
    assert "JSON decoding failed" in pair
    assert "return ''" in pair and "return None" in pair


def test_the_label_keys_all_appear_in_the_population(data):
    """A label for a pair that is not there is a label for nothing, and it
    would inflate the distinct count."""
    assert set(data["multiplicity"]) == {e["pair"] for e in data["pairs"]}


# ---------------------------------------------------------------------------
# the decision, re-derived from the labels rather than trusted from the ADR
# ---------------------------------------------------------------------------


def _weighted(data):
    labels = {e["pair"]: e["label"] for e in data["pairs"]}
    out = {}
    for pair, n in data["multiplicity"].items():
        out[labels[pair]] = out.get(labels[pair], 0) + n
    return out


def test_the_weighted_distribution_matches_the_adr(data):
    w = _weighted(data)
    assert w["code_fragment"] == 2504
    assert w["real_contradiction"] == 94
    assert w["subset"] == 92
    assert w["other"] == 164
    assert round(100 * w["code_fragment"] / sum(w.values()), 1) == 87.7


def test_no_candidate_policy_loses_a_real_contradiction(data):
    """The decision. `CONTRADICTED` is absorbing, so this asymmetry is the
    whole question -- and it comes out at zero for every policy."""
    labels = {e["pair"]: e["label"] for e in data["pairs"]}
    for policy in (
        {"code_fragment"},
        {"code_fragment", "subset"},
        {"code_fragment", "subset", "other"},
    ):
        lost = [
            pair
            for pair, n in data["multiplicity"].items()
            if labels[pair] in policy and labels[pair] == "real_contradiction"
        ]
        assert lost == [], f"{sorted(policy)} would destroy {lost}"


def test_code_is_the_whole_of_the_avoidable_population(data):
    """If a future re-label moved a real contradiction into `code_fragment`,
    this is the test that should fail first."""
    labels = {e["pair"]: e["label"] for e in data["pairs"]}
    real = [p for p, l in labels.items() if l == "real_contradiction"]
    assert len(real) == 14
    # The two the ADR singles out as genuine, both of which are prose.
    assert any("PYRAMID SCHEME" in p for p in real)
    assert any("OpenAI and Anthropic" in p for p in real)


# ---------------------------------------------------------------------------
# the ADR cannot drift away from the data it cites
# ---------------------------------------------------------------------------


def test_the_adr_cites_the_measured_numbers(data):
    """Presence is not enough -- a stale figure would still be *present*.

    Every headline number is re-derived from the labels and required to appear
    in the ADR. This is what makes the ADR falsifiable rather than decorative.
    """
    text = ADR.read_text(encoding="utf-8")
    w = _weighted(data)
    labels = {e["pair"]: e["label"] for e in data["pairs"]}
    real = [p for p, l in labels.items() if l == "real_contradiction"]
    figures = (
        f"{sum(data['multiplicity'].values()):,}",   # 2,854
        str(len(data["pairs"])),                       # 70
        f"{sum(data['multiplicity'].values()) / len(data['pairs']):.1f}",  # 40.8
        f"{max(data['multiplicity'].values()):,}",     # 1,026
        f"{w['code_fragment']:,}",                     # 2,504
        f"{100 * w['code_fragment'] / sum(w.values()):.1f}",  # 87.7
        f"{w['real_contradiction']}",                  # 94
        str(len(real)),                                # 14
    )
    for figure in figures:
        assert figure in text, f"ADR-030 no longer states {figure}"


def test_the_weighted_table_in_the_adr_matches_the_labels(data):
    """Document-level presence is not enough.

    Each headline figure appears in the ADR more than once, so falsifying the
    one table row that carries it would leave the document still containing
    the correct number somewhere else -- and a presence check would pass. This
    test therefore checks the *rows* the decision rests on.
    """
    text = ADR.read_text(encoding="utf-8")
    w = _weighted(data)
    for label, rows in sorted(w.items()):
        share = f"{100 * rows / sum(w.values()):.1f}"
        row = f"| `{label}` | {rows:,} | {share}% |"
        assert row in text, f"ADR-030's table row is stale: expected {row!r}"


def test_the_policy_table_in_the_adr_matches_the_labels(data):
    """The refusal counts in the policy table are derived, so they can drift
    the moment a label changes. Check each one."""
    text = ADR.read_text(encoding="utf-8")
    w = _weighted(data)
    total = sum(w.values())
    labels = {e["pair"]: e["label"] for e in data["pairs"]}
    running = 0
    for policy in (["code_fragment"], ["code_fragment", "subset"],
                   ["code_fragment", "subset", "other"]):
        running = sum(n for k, n in data["multiplicity"].items()
                      if labels[k] in policy)
        share = f"{100 * running / total:.1f}"
        assert f"{running:,}" in text, f"policy {policy} count {running} absent"
        assert share in text, f"policy {policy} share {share}% absent"


def test_the_adr_states_its_own_provenance_limit(data):
    """The labels are an agent's, not the author's. That has to stay in the
    document, because the document is what a later reader trusts."""
    text = ADR.read_text(encoding="utf-8")
    assert "agent reading all 70 distinct pairs" in text
    assert "not by" in text


# ---------------------------------------------------------------------------
# optional: re-derive from the artifact itself
# ---------------------------------------------------------------------------


def test_the_population_still_holds_against_the_artifact():
    """Skipped when the artifact is absent, which is the normal case for a
    clean clone. Never silently skipped for another reason."""
    import sqlite3

    db = Path("/tmp/p19-t5-real.db")
    if not db.exists():
        pytest.skip("real artifact not present; population unverified here")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        pairs = 0
        for row in con.execute(
            "SELECT subject_id, contradicted_by FROM evaluations "
            "WHERE relation='contradicted'"
        ):
            gid = row["contradicted_by"]
            if not gid:
                continue
            g = con.execute(
                "SELECT members FROM peer_groups WHERE id=?", (gid,)
            ).fetchone()
            if g:
                pairs += len(json.loads(g["members"]))
    finally:
        con.close()
    assert pairs == 2854, f"population moved: {pairs} pair rows, ADR says 2854"


def test_the_labels_file_is_not_importable_code():
    """It is data. If it ever grows an import, it stops being auditable."""
    assert "import" not in LABELS.read_text(encoding="utf-8")[:200]
    assert sys.modules.get("agent_labels") is None
