"""The harness is an instrument. An instrument that reports a violated
invariant and then exits 0 is worse than no instrument, because it converts a
failure into a green result. These tests pin that it cannot."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "run_model_loop", ROOT / "scripts" / "run_model_loop.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_model_loop"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def loop():
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    return _load()


@pytest.fixture(scope="module")
def artifact(tmp_path_factory):
    """A tiny artifact with the real schema, so the harness has something to
    read. Built by the compiler rather than hand-written SQL."""
    sys.path.insert(0, str(ROOT / "src"))
    from ganymede4.compile.compiler import SourceSpec, compile_corpus
    from ganymede4.knowledge.store import Store

    db = tmp_path_factory.mktemp("art") / "a.db"
    store = Store(str(db))
    compile_corpus(
        store,
        [
            SourceSpec(
                uri="notes.md",
                content="The runtime enforces provenance at write time.\n",
            ),
            SourceSpec(
                uri="other.md",
                content="Provenance is content addressed by hash.\n",
            ),
        ],
        transaction_time="1970-01-01T00:00:00Z",
    )
    store.close()
    return str(db)


def _counts(db):
    con = sqlite3.connect(db)
    try:
        return {
            "claims": con.execute("SELECT COUNT(*) FROM claims").fetchone()[0],
            "states": dict(
                con.execute("SELECT state, COUNT(*) FROM claims GROUP BY state")
            ),
        }
    finally:
        con.close()


def test_the_loop_leaves_the_artifact_exactly_as_it_found_it(loop, artifact):
    """The whole point of the exercise: proposals are inert."""
    before = loop.artifact_state(artifact)
    rc = loop.main([artifact, "--skip-model", "--grant-propose", "--budget", "1"])
    assert rc == 0
    assert loop.artifact_state(artifact) == before


def test_a_mutated_artifact_exits_nonzero(loop, artifact, monkeypatch, capsys):
    """Sabotage the fingerprint so the harness believes state moved, and
    confirm it refuses to report success."""
    calls = {"n": 0}

    def drifting(state):
        calls["n"] += 1
        return f"fp{calls['n']}"

    monkeypatch.setattr(loop, "state_fingerprint", drifting)
    rc = loop.main([artifact, "--skip-model", "--grant-propose", "--budget", "1"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "CHANGED: True" in out


def test_default_policy_denies_and_grant_permits(loop, artifact, capsys):
    """A system that permits nothing resists everything. Without this contrast
    the adversarial table is decoration."""
    loop.main([artifact, "--skip-model", "--budget", "1"])
    assert "permitted            : 0" in capsys.readouterr().out
    loop.main([artifact, "--skip-model", "--grant-propose", "--budget", "1"])
    assert "permitted            : 2" in capsys.readouterr().out


def test_the_report_names_every_adversarial_case(loop, artifact, tmp_path):
    out = tmp_path / "r.json"
    loop.main(
        [artifact, "--skip-model", "--grant-propose", "--budget", "1", "--json", str(out)]
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["before"] == report["after"]
    assert {r["case"] for r in report["runs"]} == {
        "benign",
        "fabricated-ids",
        "uncited",
        "path-traversal",
        "prompt-injection",
    }
    by_case = {r["case"]: r["records"][0]["disposition"] for r in report["runs"]}
    assert by_case["fabricated-ids"] == "unknown-citation"
    assert by_case["uncited"] == "uncited"
    assert by_case["path-traversal"] == "unknown-citation"


def test_injection_is_permitted_and_recorded_as_permitted(loop, artifact, tmp_path):
    """Pinned deliberately, because it is the uncomfortable result.

    The gateway authorizes who may propose; it does not read proposal text.
    If someone later adds content filtering and this flips to denied, the
    change is visible in the diff rather than silent.
    """
    out = tmp_path / "r.json"
    loop.main(
        [artifact, "--skip-model", "--grant-propose", "--budget", "1", "--json", str(out)]
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    inj = [r for r in report["runs"] if r["case"] == "prompt-injection"][0]
    assert inj["records"][0]["disposition"] == "permitted"
