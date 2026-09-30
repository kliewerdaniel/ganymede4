"""The labelling sheet must not fabricate ground truth (Phase 19 Task 3).

The scorer is the only thing standing between a heuristic opinion and a
published precision number. Three properties matter and are tested here:

1. It cannot score a sheet nobody labelled.
2. It cannot score a label it does not recognise.
3. The policy cost it reports is a real count, so a bug that reports "0 true
   contradictions lost" when the truth is 60 has to fail here.

Each test is paired with a sabotage, run against the real implementation,
recorded in ADR-027.
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "scripts")
)

import contradiction_sample as cs  # noqa: E402


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def _artifact(tmp_path: Path) -> str:
    """A tiny artifact with one contradicted subject and two peers."""
    db = tmp_path / "tiny.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE claims (id TEXT PRIMARY KEY, text TEXT NOT NULL);
        CREATE TABLE peer_groups (id TEXT PRIMARY KEY, members TEXT NOT NULL,
                                  size INTEGER NOT NULL);
        CREATE TABLE evaluations (id TEXT PRIMARY KEY, subject_id TEXT NOT NULL,
            relation TEXT NOT NULL, method TEXT NOT NULL, attested_by TEXT NOT NULL,
            contradicted_by TEXT NOT NULL, decided_at TEXT NOT NULL,
            meta TEXT NOT NULL);
        """
    )
    code = 'error(f"x")\n    return None\n'
    code2 = 'error(f"x")\n    return \'\'\n'
    prose = "It is also a pyramid scheme."
    prose2 = "THIS IS NOT A PYRAMID SCHEME!"
    conn.executemany(
        "INSERT INTO claims VALUES (?,?)",
        [("clm-sub", prose), ("clm-p1", prose2), ("clm-c1", code),
         ("clm-c2", code2)],
    )
    conn.execute(
        "INSERT INTO peer_groups VALUES (?,?,?)",
        ("agr-1", '["clm-p1","clm-sub"]', 2),
    )
    conn.execute(
        "INSERT INTO peer_groups VALUES (?,?,?)",
        ("agr-2", '["clm-c1","clm-c2","clm-sub"]', 3),
    )
    conn.execute(
        "INSERT INTO evaluations VALUES ('ev1','clm-sub','contradicted',"
        "'m','','agr-1,agr-2','t','{}')",
    )
    conn.commit()
    conn.close()
    return str(db)


def _sheet(tmp_path: Path, labels: dict[int, str]) -> str:
    path = tmp_path / "sheet.csv"
    rows = []
    for i in range(1, 7):
        rows.append(
            {
                "pair_id": f"P{i:04d}",
                "stratum": "code" if i <= 3 else "prose",
                "subject_id": f"clm-{i}",
                "subject_text": "a",
                "peer_id": f"clm-{i}b",
                "peer_text": "b",
                "label": labels.get(i, ""),
            }
        )
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(cs.FIELDS))
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


# --------------------------------------------------------------------------
# the sheet must be unlabelled
# --------------------------------------------------------------------------
def test_build_never_invents_a_label(tmp_path):
    rows = cs.build_rows(
        [
            {"subject_id": "a", "subject_text": "x", "peer_id": "b", "peer_text": "y"},
            {"subject_id": "c", "subject_text": "z", "peer_id": "d", "peer_text": "w"},
        ],
        per_stratum=10,
        seed=0,
    )
    assert rows, "build produced no rows"
    assert all(r["label"] == "" for r in rows), "a label was invented"
    assert all(r["pair_id"] for r in rows), "rows must be addressable"


def test_build_is_deterministic_for_a_seed():
    pairs = [
        {"subject_id": f"s{i}", "subject_text": f"t{i}", "peer_id": f"p{i}",
         "peer_text": f"q{i}"}
        for i in range(40)
    ]
    assert cs.build_rows(pairs, 10, seed=7) == cs.build_rows(pairs, 10, seed=7)


def test_build_is_order_independent():
    """Reversed input order must give the same sheet: same determinism rule
    as the compiler's Merkle root, for the same reason."""
    pairs = [
        {"subject_id": f"s{i}", "subject_text": f"t{i}", "peer_id": f"p{i}",
         "peer_text": f"q{i}"}
        for i in range(40)
    ]
    assert cs.build_rows(pairs, 10, seed=7) == cs.build_rows(
        list(reversed(pairs)), 10, seed=7
    )


def test_reads_pairs_and_excludes_the_subject_from_its_own_group(tmp_path):
    pairs = cs.read_contradicted_pairs(_artifact(tmp_path))
    # agr-1 has clm-p1 + clm-sub; agr-2 has clm-c1 + clm-c2 + clm-sub.
    # The subject appears in both and must never pair with itself.
    assert len(pairs) == 3
    for pair in pairs:
        assert pair["subject_id"] != pair["peer_id"]


# --------------------------------------------------------------------------
# the scorer must refuse to guess
# --------------------------------------------------------------------------
def test_refuses_an_unlabelled_sheet(tmp_path, capsys):
    assert cs.score(_sheet(tmp_path, {}), "none") == 1
    assert "0 labelled" in capsys.readouterr().out


def test_refuses_an_unrecognised_label(tmp_path, capsys):
    path = _sheet(tmp_path, {1: "looks_fine_to_me"})
    assert cs.score(path, "none") == 1
    assert "unrecognised" in capsys.readouterr().out


def test_a_blank_label_is_not_a_zero_label(tmp_path, capsys):
    """Partially filled sheets must not silently score the filled rows as if
    they were the whole sample."""
    assert cs.score(_sheet(tmp_path, {1: "real_contradiction"}), "none") == 0
    assert "unlabelled 5" in capsys.readouterr().out


# --------------------------------------------------------------------------
# the policy cost must be a real count
# --------------------------------------------------------------------------
def test_policy_reports_true_contradictions_it_loses(tmp_path, capsys):
    """A prose pair labelled real_contradiction must survive `no-code`.

    This is the asymmetry ADR-027 rests on: CONTRADICTED is absorbing, so a
    policy that discards a real finding is expensive in a way one that keeps a
    false finding is not.
    """
    labels = {
        1: "code_fragment", 2: "code_fragment", 3: "code_fragment",
        4: "real_contradiction", 5: "real_contradiction", 6: "boilerplate",
    }
    assert cs.score(_sheet(tmp_path, labels), "no-code") == 0
    out = capsys.readouterr().out
    assert "TRUE contradictions it would lose: 0 of 2" in out
    assert "would refuse : 3 of 6" in out


def test_a_policy_that_loses_a_real_contradiction_says_so(tmp_path, capsys):
    """Labelling a code-stratum pair as real must show up as a real loss."""
    labels = {
        1: "real_contradiction", 2: "code_fragment", 3: "code_fragment",
        4: "real_contradiction", 5: "real_contradiction", 6: "boilerplate",
    }
    assert cs.score(_sheet(tmp_path, labels), "no-code") == 0
    out = capsys.readouterr().out
    assert "TRUE contradictions it would lose: 1 of 3" in out
    assert "P0001" in out


def test_policy_none_loses_nothing(tmp_path, capsys):
    labels = {i: "real_contradiction" for i in range(1, 7)}
    assert cs.score(_sheet(tmp_path, labels), "none") == 0
    out = capsys.readouterr().out
    assert "TRUE contradictions it would lose: 0 of 6" in out
    assert "would refuse : 0 of 6" in out


# --------------------------------------------------------------------------
# stratification is a sampling frame, and must be honest about it
# --------------------------------------------------------------------------
def test_non_latin_fraction_counts_letters_not_codepoints():
    """U+2019 is punctuation, not a script.

    Measured on all 2,854 contradicted pair texts in the real artifact: the
    two candidate formulas -- "share of codepoints above U+007F" versus
    "share of *letters* above U+007F" -- disagree on **zero** texts, so at
    the 0.2 bucket threshold this choice is currently unreachable on this
    corpus. That is worth stating plainly: an earlier draft of this file
    claimed the raw-codepoint form misfiled English prose in the non-Latin
    bucket, and that claim was wrong. English prose with curly apostrophes
    tops out around 0.06, nowhere near the threshold.

    So this test pins the function directly rather than trying to route
    through `stratum_of`, because routing through the bucket requires
    punctuation-dense synthetic text to reach 0.2 at all. The correct
    implementation is kept because the category check is the honest one and
    costs nothing; it is not kept because this corpus proved it necessary.
    """
    text = "“don’t” — it’s ‘fine’"
    assert sum(1 for c in text if ord(c) > 127) / len(text) > 0.2, (
        "fixture must be punctuation-dense enough to discriminate"
    )
    assert cs._non_latin_fraction(text) == 0.0


def test_curly_apostrophes_do_not_bucket_english_as_non_latin():
    text = "I don’t think that’s right, and I’m not going to change my mind about it"
    assert cs.stratum_of(text, "Agreed.") == "prose"


def test_real_non_latin_text_is_bucketed_as_non_latin():
    assert cs.stratum_of("Я да — та", "Я нет") == "non-latin"


def test_indented_multiline_code_is_bucketed_as_code():
    assert cs.stratum_of("def f():\n    return None\n", "def f():\n    return ''\n") == "code"


def test_stratifier_never_returns_an_unexpected_bucket():
    for text in ["", "   ", "{}", "hello world", "a\nb\nc"]:
        assert cs.stratum_of(text, "x") in ("code", "prose", "non-latin")