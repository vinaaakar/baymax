import json

import pytest

from baymax.coding.agent import CodingAgent, parse_edits
from baymax.coding.files import EditError, FileOps
from baymax.coding.project import inspect_project
from baymax.hostos.process import ProcessManager
from baymax.security.workspace import Workspace, WorkspaceViolation

BUGGY = "def add(a, b):\n    return a - b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


@pytest.fixture(autouse=True)
def venv_python_first(monkeypatch):
    """Make `python3` resolve to the interpreter running the tests (which has pytest installed)."""
    import os
    import sys
    from pathlib import Path

    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(BUGGY)
    (root / "tests" / "test_calc.py").write_text(TEST)
    (root / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    return root


def noop(*a, **k):
    return None


def test_project_detection(repo, tmp_path):
    info = inspect_project(repo)
    assert info.languages["Python"] == 2 and ["python3", "-m", "pytest", "-q"] in info.test_commands
    js = tmp_path / "js"
    js.mkdir()
    (js / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}, "devDependencies": {"react": "1", "vite": "1"}}))
    i = inspect_project(js)
    assert ["npm", "test"] in i.test_commands and "react" in i.frameworks
    sb = tmp_path / "sb"
    sb.mkdir()
    (sb / "pom.xml").write_text("<project><parent><artifactId>spring-boot-starter-parent</artifactId></parent></project>")
    (sb / "mvnw").write_text("#!/bin/sh")
    i = inspect_project(sb)
    assert "spring-boot" in i.frameworks and ["./mvnw", "-q", "test"] in i.test_commands


async def test_agent_fixes_failing_tests_with_bounded_loop(repo, tmp_path):
    ops = FileOps(Workspace(repo), tmp_path / "backups", "t1")
    calls = []

    async def chat(messages):
        calls.append(messages)
        if len(calls) == 1:  # first attempt: wrong fix
            return json.dumps({"plan": "try", "edits": [{"path": "calc.py", "search": "return a - b", "replace": "return a * b"}]})
        return json.dumps({"plan": "fix", "edits": [{"path": "calc.py", "search": "return a * b", "replace": "return a + b"}]})

    agent = CodingAgent(ProcessManager(), timeout=120, max_attempts=3)
    report = await agent.run(repo, ops, "fix the failing test", chat, noop, lambda: None)
    assert report.baseline is not None and not report.baseline.passed  # really ran and failed
    assert report.status == "fixed" and report.final.passed and len(report.attempts) == 2
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert report.changes[0]["path"] == "calc.py" and report.changes[0]["backup"]
    assert "Final `python3 -m pytest -q`: passed" in report.summary()
    assert "assert" in report.baseline.output_tail  # the baseline really failed on the assertion


async def test_agent_stops_after_max_attempts(repo, tmp_path):
    ops = FileOps(Workspace(repo), tmp_path / "b", "t2")

    async def chat(messages):
        return json.dumps({"edits": [{"path": "calc.py", "search": "def add", "replace": "def add"}]})

    report = await CodingAgent(ProcessManager(), max_attempts=2).run(repo, ops, "fix", chat, noop, lambda: None)
    assert report.status == "not_fixed" and len(report.attempts) == 2 and not report.final.passed


async def test_agent_without_model_changes_nothing(repo, tmp_path):
    ops = FileOps(Workspace(repo), tmp_path / "b", "t3")
    report = await CodingAgent(ProcessManager()).run(repo, ops, "fix it", None, noop, lambda: None)
    assert report.status == "model_unavailable" and report.changes == []
    assert (repo / "calc.py").read_text() == BUGGY


async def test_agent_rejects_edits_outside_repo(repo, tmp_path):
    (tmp_path / "victim.txt").write_text("safe")
    ops = FileOps(Workspace(repo), tmp_path / "b", "t4")

    async def chat(messages):
        return json.dumps({"files": [{"path": "../victim.txt", "content": "pwned"}]})

    report = await CodingAgent(ProcessManager(), max_attempts=1).run(repo, ops, "fix", chat, noop, lambda: None)
    assert (tmp_path / "victim.txt").read_text() == "safe"
    assert "escapes" in report.attempts[0]["errors"][0]


def test_file_ops_edit_rules_and_restore(repo, tmp_path):
    ops = FileOps(Workspace(repo), tmp_path / "b", "t5")
    with pytest.raises(EditError):
        ops.edit("calc.py", "nonexistent text", "x")
    (repo / "dup.py").write_text("x = 1\nx = 1\n")
    with pytest.raises(EditError):
        ops.edit("dup.py", "x = 1", "x = 2")
    with pytest.raises(EditError):
        ops.write("calc.py", "new", overwrite=False)
    with pytest.raises(WorkspaceViolation):
        ops.read("/etc/passwd")
    ops.edit("calc.py", "a - b", "a + b")
    ops.write("new.py", "print(1)\n")
    assert ops.restore_all() == 2
    assert (repo / "calc.py").read_text() == BUGGY and not (repo / "new.py").exists()


def test_parse_edits_validation():
    with pytest.raises(EditError):
        parse_edits("no json")
    with pytest.raises(EditError):
        parse_edits('{"edits": []}')
    with pytest.raises(EditError):
        parse_edits(json.dumps({"files": [{"path": f"f{i}", "content": ""} for i in range(25)]}))
    plan, e, f = parse_edits('Sure! {"plan": "p", "edits": [{"path": "a", "search": "b", "replace": "c"}]}')
    assert plan == "p" and e[0]["path"] == "a" and f == []


async def test_missing_test_runner_is_reported_as_environment_problem(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))  # python3 not found at all
    ops = FileOps(Workspace(repo), tmp_path / "b", "t6")
    report = await CodingAgent(ProcessManager()).run(repo, ops, "fix", None, noop, lambda: None)
    assert report.status == "environment_error" and "not installed" in report.summary()
    assert report.changes == [] and (repo / "calc.py").read_text() == BUGGY
