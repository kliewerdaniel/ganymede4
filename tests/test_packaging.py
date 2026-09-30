"""The shipped copies must not drift from the originals.

ADR-025 ships ``audit_provenance.py`` and ``build_artifact.py`` twice: once in
``scripts/``, which is the repository's working copy, and once in
``ganymede4/_scripts/``, which is what a wheel installs. Two copies of a
verification script is a real hazard -- an auditor fixed in one place and
stale in the other would report on artifacts under one set of rules and be
cited as evidence under another.

Duplication is accepted here because the alternative is worse: making the
console locate scripts relative to the installed module, which is precisely
the bug this exercise exists to fix. So the copies are checked instead, and
the check is a test rather than a convention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SHIPPED = REPO / "src" / "ganymede4" / "_scripts"
SCRIPTS = ("audit_provenance.py", "build_artifact.py")


@pytest.mark.parametrize("name", SCRIPTS)
def test_shipped_copy_is_byte_identical(name):
    original = (REPO / "scripts" / name).read_bytes()
    shipped = (SHIPPED / name).read_bytes()
    assert shipped == original, (
        f"{name} differs between scripts/ and ganymede4/_scripts/. The shipped "
        "copy is what an installed console runs, so a fix applied to only one "
        "of them means installed users get different behaviour from anyone "
        "testing in a checkout."
    )


@pytest.mark.parametrize("name", SCRIPTS)
def test_shipped_copy_is_listed_for_packaging(name):
    text = (REPO / "pyproject.toml").read_text()
    assert "_scripts/*.py" in text, (
        "the shipped scripts directory is not declared as package data, so it "
        "will not be in the wheel"
    )
    assert (SHIPPED / name).is_file()


def test_the_shipped_auditor_is_still_independent():
    """Shipping the auditor inside the package must not make it depend on it.

    The reason the auditor exists is that it was written without reading the
    package. Copying it into the package is a packaging decision; it does not
    change what it imports.
    """
    text = (SHIPPED / "audit_provenance.py").read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert not stripped.startswith(("import ganymede4",
                                            "from ganymede4")), (
                f"the shipped auditor imports the package: {stripped}"
            )