import pytest

from baymax.creation3d.classifier import RequestClassifier
from baymax.creation3d.registry import GeneratorRegistry
from baymax.db import Database
from baymax.intent.router import IntentRouter, RouteContext, parse_model_proposal
from baymax.memory.store import MemoryRejected, MemoryStore, sensitive_reason
from baymax.tools.system_tools import OpenAppTool

R = IntentRouter(RequestClassifier(GeneratorRegistry()))


@pytest.mark.parametrize("text,kind,tool", [
    ("Emergency stop!", "estop", None),
    ("stop", "cancel", None),
    ("never mind", "cancel", None),
    ("Baymax, go to sleep", "sleep", None),
    ("wake up", "wake", None),
    ("remember that I prefer metric units", "memory_add", None),
    ("what do you remember about me?", "memory_list", None),
    ("forget that I like jazz", "memory_forget", None),
    ("forget everything", "memory_clear", None),
    ("open Safari", "tool", "apps.open"),
    ("launch visual studio code", "tool", "apps.open"),
    ("go to https://example.com/docs", "tool", "apps.open_url"),
    ("open the website example.com", "tool", "apps.open_url"),
    ("make a red sports car", "tool", "creation3d.create"),
    ("create a 3D model of a giraffe", "tool", "creation3d.create"),
    ("build me a house with two floors", "tool", "creation3d.create"),
    ("generate an image of a sunset over mountains", "tool", "image.generate"),
    ("research the latest whisper.cpp releases", "tool", "research.web"),
    ("search the web for M4 MacBook Air memory bandwidth", "tool", "research.web"),
    ("simulate a projectile at 30 m/s and 45 degrees", "tool", "simulation.run"),
    ("draw a flowchart: plan -> build -> test -> ship", "tool", "documents.diagram"),
    ("write a technical document about our pairing protocol", "tool", "documents.write"),
    ("run the tests in ./myrepo", "tool", "coding.agent"),
    ("inspect the repository at ./myrepo", "tool", "coding.inspect"),
    ("run `npm test` in web", "tool", "shell.run"),
    ("send an email to bob@example.com saying the build is green", "tool", "mail.send"),
    ("click at 100, 200", "tool", "desktop.click"),
    ("move the mouse to 400 300", "tool", "desktop.move_cursor"),
    ("scroll down 3", "tool", "desktop.scroll"),
    ('type "hello world" into Notes', "tool", "desktop.type_text"),
    ("take a screenshot", "tool", "desktop.screenshot"),
    ("how are you today?", "chat", None),
    ("what's the capital of France", "chat", None),
])
def test_routing(text, kind, tool):
    i = R.route(text)
    assert i.kind == kind, (text, i)
    assert i.tool == tool, (text, i)


def test_args_extracted():
    assert R.route("open vscode").args == {"app": "Visual Studio Code"}
    assert R.route("scroll up").args == {"dy": 5}
    assert R.route("simulate a pendulum").args["request"] == "simulate a pendulum"
    m = R.route("send an email to bob@example.com subject \"Status\" saying all good")
    assert m.args == {"to": "bob@example.com", "subject": "Status", "body": "all good"}
    assert R.route("remember that I prefer Celsius").args["content"] == "I prefer Celsius"


def test_model_edits_need_active_model():
    assert R.route("make the body red").kind == "chat"
    i = R.route("make the body red", RouteContext(active_model="abc123abc123"))
    assert i.tool == "creation3d.modify" and i.args["artifact_id"] == "abc123abc123"
    for t in ("export this as GLB", "view from the top", "undo", "increase the wheel size", "make it more detailed"):
        assert R.route(t, RouteContext(active_model="abc123abc123")).tool == "creation3d.modify", t
    # a new creation request is not an edit
    assert R.route("make a blue chair", RouteContext(active_model="abc123abc123")).tool == "creation3d.create"


def test_model_proposals_are_validated():
    tools = {"apps.open": OpenAppTool()}
    assert parse_model_proposal('{"tool": "apps.open", "args": {"app": "Notes"}}', tools).tool == "apps.open"
    # unknown tools never become tool calls
    assert parse_model_proposal('{"tool": "shell.exec", "args": {"cmd": "rm -rf /"}}', tools).kind == "chat"
    # bad args -> clarification
    assert parse_model_proposal('{"tool": "apps.open", "args": {"app": 5}}', tools).kind == "clarify"
    assert parse_model_proposal('{"tool": null, "reply": "Hello!"}', tools).reply == "Hello!"
    assert parse_model_proposal("not json at all", tools).kind == "chat"


# ------------------------------------------------------------------- memory
def test_memory_controls(tmp_path):
    m = MemoryStore(Database(tmp_path / "m.db"))
    a = m.add("I prefer metric units")
    assert m.add("i prefer metric   units").id == a.id  # dedupe
    m.add("My favourite colour is crimson")
    assert len(m.list()) == 2
    assert m.update(a.id, "I prefer imperial units").content == "I prefer imperial units"
    assert m.delete_matching("crimson") == 1
    assert m.delete(a.id) and not m.list()
    m.add("x y z")
    assert m.clear() == 1


@pytest.mark.parametrize("text", [
    "my password is hunter2", "the api key is sk-abcdefghijklmnopqrstuvwxyz", "card 4111 1111 1111 1111", "ssn 123-45-6789",
    "-----BEGIN RSA PRIVATE KEY-----", "my github token ghp_abcdefghijklmnopqrstuvwx", "my PIN is 1234",
])
def test_memory_rejects_sensitive(tmp_path, text):
    m = MemoryStore(Database(tmp_path / "m.db"))
    assert sensitive_reason(text)
    with pytest.raises(MemoryRejected):
        m.add(text)
    assert m.list() == []


def test_memory_limits(tmp_path):
    m = MemoryStore(Database(tmp_path / "m.db"))
    with pytest.raises(MemoryRejected):
        m.add("   ")
    with pytest.raises(MemoryRejected):
        m.add("x" * 600)
