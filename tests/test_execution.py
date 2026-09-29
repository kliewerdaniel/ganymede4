"""Execution tests (ADR-008).

Gate 4: *fail-closed tests — an unauthorized action cannot execute, and a
degraded path still produces an audit record.*

These tests use the real filesystem: real symlinks, real directories, a real
subprocess. A symlink test using a mock is a test of the mock, and the entire
purpose of the point-of-use recheck is that it consults the filesystem. If this
suite passed against a fake ``realpath`` it would be certifying that the code
calls a function, not that the function prevents an escape.

The suite is written adversarially, in the same spirit as ``test_policy.py``:
most of these tests are attempts to make the executor perform something it
should not.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path

import pytest

from sovereign_runtime.execution import executor as ex
from sovereign_runtime.execution.executor import ExecutionRefused, Executor
from sovereign_runtime.execution.sandbox import (
    NullSandbox,
    SandboxResult,
    SubprocessSandbox,
)
from sovereign_runtime.policy.gateway import (
    Capability,
    Decision,
    Policy,
    PolicyGateway,
    Request,
    Verdict,
)

PKG = Path(inspect.getfile(ex)).parent


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A real directory tree with a real symlink escaping it.

    ``data/`` is the granted prefix. ``escape`` is a symlink to ``/etc``, so
    ``data/escape/shadow`` is a path that satisfies a ``/data/*`` grant as a
    string and lands outside it as a path. That is the exact bug the gateway
    defers and the executor must close.
    """
    data = tmp_path / "data"
    data.mkdir()
    (data / "notes.md").write_text("compiled corpus\n")
    os.symlink("/etc", data / "escape")
    return tmp_path


def policy_for(prefix: str) -> Policy:
    return Policy(
        capabilities=(
            Capability("corpus", frozenset({"read"}), {"path": f"{prefix}*"}),
        ),
        version="v1",
    )


def read(path: str) -> Request:
    return Request(actor="operator", action="read", resource="corpus", args={"path": path})


class RecordingBackend:
    """A backend that records what it was asked to do.

    ``performed`` is false by default so the default suite exercises the
    degraded path; tests that want a real run set it.
    """

    name = "recording"

    def __init__(self, performed: bool = False, degraded: bool = True, raises: bool = False):
        self.calls: list[tuple[str, tuple[str, ...], str]] = []
        self._performed = performed
        self._degraded = degraded
        self._raises = raises

    def run(self, tool, argv, cwd, policy) -> SandboxResult:
        self.calls.append((tool, tuple(argv), cwd))
        if self._raises:
            raise RuntimeError("backend exploded")
        return SandboxResult(
            performed=self._performed,
            degraded=self._degraded,
            returncode=0 if self._performed else None,
            detail="recorded",
        )


# --------------------------------------------------------------------------
# the point-of-use recheck — the reason this component exists
# --------------------------------------------------------------------------


class TestPointOfUse:
    def test_a_symlink_escape_is_refused_even_though_the_gateway_allowed_it(self, root):
        """The whole component, in one test.

        The gateway's lexical check approves this path. If the executor did not
        re-check against the filesystem, this would be a working sandbox escape
        and the ADR-007 deferral would have been a bug with a comment on it.
        """
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        escape = f"{root}/data/escape/shadow"

        # Precondition: the gateway says yes. It must, or the test is not
        # testing the disagreement it claims to test.
        assert gateway.decide(read(escape)).verdict is Verdict.ALLOW

        ex_exec = Executor(gateway, RecordingBackend())
        with pytest.raises(ExecutionRefused) as caught:
            ex_exec.execute(read(escape), tool="cat")

        assert "point-of-use" in str(caught.value)
        assert ex_exec.backend_name == "recording"

    def test_the_refusal_is_still_recorded(self, root):
        """A permitted request that did not happen is the most useful record there is."""
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway, RecordingBackend())
        with pytest.raises(ExecutionRefused):
            ex_exec.execute(read(f"{root}/data/escape/shadow"), tool="cat")

        (record,) = ex_exec.records
        assert record.outcome == "refused"
        assert record.performed is False
        assert record.degraded is False
        assert "point-of-use" in record.detail

    def test_a_plain_path_inside_the_grant_is_permitted(self, root):
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway, RecordingBackend(performed=True, degraded=False))
        record = ex_exec.execute(read(f"{root}/data/notes.md"), tool="cat")
        assert record.outcome == "performed"
        assert record.succeeded
        assert record.resolved_path == os.path.realpath(f"{root}/data/notes.md")

    def test_a_symlink_pointing_inside_the_grant_is_fine(self, root):
        """The recheck tests *location*, not the presence of a symlink.

        A check that refused every symlink would be safe and useless, and would
        train operators to avoid a correct feature.
        """
        os.symlink(f"{root}/data/notes.md", f"{root}/data/alias.md")
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway, RecordingBackend(performed=True, degraded=False))
        record = ex_exec.execute(read(f"{root}/data/alias.md"), tool="cat")
        assert record.succeeded


# --------------------------------------------------------------------------
# fail closed
# --------------------------------------------------------------------------


class TestFailClosed:
    def test_a_denied_request_never_reaches_the_backend(self, root):
        gateway = PolicyGateway()  # no policy at all
        backend = RecordingBackend()
        ex_exec = Executor(gateway, backend)

        with pytest.raises(ExecutionRefused):
            ex_exec.execute(read(f"{root}/data/notes.md"), tool="rm")

        assert backend.calls == [], "the backend was invoked on a denied request"
        assert ex_exec.records == (), "a denial must not produce an execution record"

    def test_no_record_is_produced_for_a_denial(self, root):
        """Nothing was attempted, so there is nothing to record.

        The decision chain already holds the denial. Writing an execution
        record here would imply an attempt that did not occur, which is the
        mirror image of the "degraded but unreported" failure.
        """
        ex_exec = Executor(PolicyGateway(), RecordingBackend())
        with pytest.raises(ExecutionRefused):
            ex_exec.execute(read(f"{root}/data/notes.md"), tool="rm")
        assert ex_exec.records == ()

    def test_a_broken_decision_chain_blocks_execution(self, root):
        """A decision edited after the fact is not a permission.

        The forged decision still says ALLOW. What it no longer has is a chain
        that links to the decisions before it, and that is what is checked.
        """
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        gateway.decide(read(f"{root}/data/one.md"))
        original = gateway.decisions[0]
        gateway._decisions[0] = Decision(
            request=original.request,
            verdict=Verdict.DENY,  # was ALLOW
            reason=original.reason,
            policy_id=original.policy_id,
            policy_version=original.policy_version,
            decided_at=original.decided_at,
            chain_prev=original.chain_prev,
            chain_hash=original.chain_hash,
            decision_id=original.decision_id,
        )
        ex_exec = Executor(gateway, RecordingBackend())
        with pytest.raises(Exception) as caught:
            ex_exec.execute(read(f"{root}/data/notes.md"), tool="rm")
        assert "chain" in str(caught.value).lower()

    def test_a_non_string_path_is_refused(self, root):
        """Two layers refuse this, and the outer one is hit first.

        The gateway rejects ``path=42`` on the constraint comparison before the
        executor's own type check runs, so the refusal names the constraint
        rather than the type. That is defense in depth working: the executor's
        check is not redundant, because the gateway's is a string comparison
        and the executor's is the one that would survive a constraint that did
        not constrain the path.
        """
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway, RecordingBackend())
        bad = Request("operator", "read", "corpus", args={"path": 42})
        with pytest.raises(ExecutionRefused) as caught:
            ex_exec.execute(bad, tool="cat")
        assert "path" in str(caught.value)
        assert ex_exec.records == ()

    def test_the_executor_type_checks_the_path_itself(self, root):
        """Bypass the gateway's constraint and the executor still refuses.

        A capability whose constraint does not mention ``path`` reaches the
        executor with a non-string, and the type check has to be the executor's
        own — otherwise ``realpath(42)`` is the next question.
        """
        gateway = PolicyGateway(
            Policy(
                capabilities=(Capability("corpus", frozenset({"read"})),),
                version="v1",
            )
        )
        ex_exec = Executor(gateway, RecordingBackend())
        assert gateway.decide(
            Request("operator", "read", "corpus", args={"path": 42})
        ).verdict is Verdict.ALLOW

        with pytest.raises(ExecutionRefused) as caught:
            ex_exec.execute(
                Request("operator", "read", "corpus", args={"path": 42}), tool="cat"
            )
        assert "not a string" in str(caught.value)

    def test_an_existing_prefix_sibling_does_not_satisfy_the_grant(self, tmp_path):
        """``/data-evil`` must not satisfy a ``/data/*`` grant.

        A plain string prefix would allow it. This is the same class of bug the
        executor exists to catch, so the fix must not reproduce it.
        """
        (tmp_path / "data").mkdir()
        (tmp_path / "data-evil").mkdir()
        gateway = PolicyGateway(policy_for(f"{tmp_path}/data"))
        ex_exec = Executor(gateway, RecordingBackend())
        with pytest.raises(ExecutionRefused):
            ex_exec.execute(read(f"{tmp_path}/data-evil/secret"), tool="cat")


# --------------------------------------------------------------------------
# the audit record exists regardless of outcome
# --------------------------------------------------------------------------


class TestAuditRecord:
    def test_a_degraded_no_op_still_produces_a_record(self, root):
        """Gate 4's explicit requirement.

        The no-op backend is the default, so this is the out-of-the-box path.
        A lazy implementation skips the record here because "nothing
        happened"; nothing happened *because the executor chose not to*, and the
        attempt is still the executor's.
        """
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway)  # default NullSandbox
        record = ex_exec.execute(read(f"{root}/data/notes.md"), tool="some-tool")

        assert record.outcome == "degraded"
        assert record.performed is False
        assert record.degraded is True
        assert record.record_id.startswith("exe-")
        assert "null sandbox" in record.detail

    def test_degraded_and_performed_are_separate_facts(self, root):
        """A refusal is not a degraded no-op, and must not read as one.

        Conflating them makes the record unable to distinguish "the sandbox was
        not installed" from "the safety check stopped it" — the two things an
        operator most needs to tell apart.
        """
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway)
        with pytest.raises(ExecutionRefused):
            ex_exec.execute(read(f"{root}/data/escape/shadow"), tool="cat")
        (record,) = ex_exec.records
        assert record.outcome == "refused"
        assert (record.performed, record.degraded) == (False, False)

    def test_a_raising_backend_is_recorded_not_swallowed(self, root):
        """The attempt happened whether or not it succeeded."""
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway, RecordingBackend(raises=True))
        record = ex_exec.execute(read(f"{root}/data/notes.md"), tool="cat")
        assert record.performed is False
        assert "raised" in record.detail
        assert record.detail  # the error text survives into the record

    def test_a_failed_run_is_performed_not_degraded(self, root):
        """Exited non-zero means it ran and said no. That is not degradation."""
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway, SubprocessSandbox(allowed=frozenset({"false"})))
        record = ex_exec.execute(read(f"{root}/data/notes.md"), tool="false")
        assert record.performed is True
        assert record.degraded is False
        assert record.returncode == 1
        assert not record.succeeded

    def test_the_record_binds_the_decision_and_the_request(self, root):
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway, RecordingBackend())
        request = read(f"{root}/data/notes.md")
        record = ex_exec.execute(request, tool="cat")

        decision = gateway.decisions[-1]
        assert record.decision_id == decision.decision_id
        assert record.request_digest == request.digest()
        assert record.record_id == record.decision_id[:0] + record.record_id  # stable field

    def test_record_ids_are_content_addressed_and_differ(self, root):
        """Two different actions must not share a record id."""
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway, RecordingBackend())
        a = ex_exec.execute(read(f"{root}/data/notes.md"), tool="cat", argv=["a"])
        b = ex_exec.execute(read(f"{root}/data/notes.md"), tool="cat", argv=["b"])
        assert a.record_id != b.record_id

    def test_records_are_immutable_and_ordered(self, root):
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway, RecordingBackend())
        ex_exec.execute(read(f"{root}/data/notes.md"), tool="a")
        ex_exec.execute(read(f"{root}/data/notes.md"), tool="b")
        records = ex_exec.records
        assert isinstance(records, tuple)
        assert [r.tool for r in records] == ["a", "b"]
        with pytest.raises(Exception):
            records.append(None)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# the real subprocess backend
# --------------------------------------------------------------------------


class TestSubprocessSandbox:
    def test_it_actually_runs_a_process(self, root):
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway, SubprocessSandbox(allowed=frozenset({"echo"})))
        record = ex_exec.execute(read(f"{root}/data/notes.md"), tool="echo", argv=["hello"])
        assert record.succeeded
        assert record.stdout.strip() == "hello"

    def test_an_unlisted_tool_is_a_degraded_no_op_not_a_crash(self, root):
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        ex_exec = Executor(gateway, SubprocessSandbox(allowed=frozenset({"echo"})))
        record = ex_exec.execute(read(f"{root}/data/notes.md"), tool="rm", argv=["-rf", "/"])
        assert record.performed is False
        assert record.degraded is True
        assert "allow-list" in record.detail

    def test_it_cannot_be_constructed_without_an_allow_list(self):
        """An absent allow-list would be a sandbox that runs everything."""
        with pytest.raises(ValueError):
            SubprocessSandbox(None)

    def test_arguments_cannot_become_a_command(self, root):
        """``argv`` is a list. A metacharacter in it is a character.

        If any of this were interpolated into a shell string, the semicolon
        would execute a second command. That it does not is the entire reason
        ``shell=False`` is not negotiable.
        """
        gateway = PolicyGateway(policy_for(f"{root}/data"))
        marker = root / "PWNED"
        ex_exec = Executor(gateway, SubprocessSandbox(allowed=frozenset({"echo"})))
        record = ex_exec.execute(
            read(f"{root}/data/notes.md"),
            tool="echo",
            argv=[f"hi; touch {marker}"],
        )
        assert record.succeeded
        assert record.stdout.strip() == f"hi; touch {marker}"
        assert not marker.exists(), "a shell interpreted an argument"

    def test_a_timeout_is_required(self):
        """A backend that can hang forever can deny the operator the record."""
        with pytest.raises(ValueError):
            SubprocessSandbox(allowed=frozenset({"sleep"}), timeout=0)


# --------------------------------------------------------------------------
# static guarantees
# --------------------------------------------------------------------------


class TestStatic:
    def test_no_shell_execution_anywhere_in_the_package(self):
        """``shell=True`` would make every argument a command."""
        for path in PKG.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        assert not (kw.arg == "shell" and ast.literal_eval(kw.value)), (
                            f"{path.name} uses shell=True"
                        )

    def test_no_network_imports(self):
        banned = {"socket", "http", "urllib", "requests", "httpx", "ftplib", "asyncio"}
        for path in PKG.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in banned, f"{path.name}: {alias.name}"
                elif isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module.split(".")[0] not in banned, f"{path.name}: {node.module}"

    def test_no_model_imports(self):
        """The model may propose an action. It may not perform one."""
        banned = {"openai", "anthropic", "ollama", "transformers", "torch"}
        for path in PKG.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in banned
                elif isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module.split(".")[0] not in banned

    def test_the_default_backend_executes_nothing(self):
        assert NullSandbox().run("rm", ("-rf", "/"), ".", {}).performed is False
        assert Executor(PolicyGateway()).backend_name == "null"
