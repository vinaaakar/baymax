"""Developer commands end to end: real files, a real JDK, real pdftotext, the real repository."""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from baymax.assistant import Assistant
from baymax.tasks.models import TaskStatus
from baymax.tools import dev_tools

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
async def assistant(services):
    a = Assistant(services)
    a.start()
    yield a
    await a.stop()


async def run(services, assistant, text, timeout=60):
    r = await assistant.handle_text(text)
    assert "taskId" in r, r
    return await services.tasks.wait(r["taskId"], timeout)


@pytest.fixture
def projects(tmp_path, monkeypatch):
    dev = tmp_path / "Developer"
    (dev / "BAYMAX" / ".git").mkdir(parents=True)
    (dev / "other-app").mkdir()
    monkeypatch.setattr(dev_tools, "DEFAULT_SEARCH_ROOTS", (str(dev),))
    return dev


async def test_open_my_repository_in_vs_code_is_verified_by_window_title(services, assistant, fake_desktop, projects):
    t = await run(services, assistant, "Open my BAYMAX repository in VS Code")
    assert t.status is TaskStatus.COMPLETED and t.verified, t.error
    assert ("open_path", "Visual Studio Code", str((projects / "BAYMAX").resolve())) in fake_desktop.calls
    assert services.state["current_project"] == str((projects / "BAYMAX").resolve())


async def test_open_project_ambiguous_and_missing(services, assistant, projects):
    (projects / "work" / "baymax").mkdir(parents=True)
    (projects / "work" / "baymax" / ".git").mkdir()
    t = await run(services, assistant, "open baymax in vscode")
    assert t.status is TaskStatus.FAILED and "more than one folder" in (t.error or "")
    t = await run(services, assistant, "open zebra-tracker in vscode")
    assert t.status is TaskStatus.FAILED and "couldn't find a project folder" in t.result["summary"]


async def test_find_function_in_the_real_repository(services, assistant):
    services.state["current_project"] = str(REPO)
    t = await run(services, assistant, "Find the function responsible for cursor tracking")
    assert t.status is TaskStatus.COMPLETED, t.error
    best = t.result["data"]["hits"][0]
    assert best["name"] == "startCursorTracking" and best["file"].endswith("desktop/electron/main.ts")
    assert all("dist-electron" not in h["file"] for h in t.result["data"]["hits"])


async def test_explain_error_from_clipboard_with_source_context(services, assistant, fake_desktop, tmp_path):
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "Main.java").write_text("public class Main {\n  public static void main(String[] a) {\n    System.out.printn(\"hi\");\n  }\n}\n")
    services.state["current_project"] = str(proj)
    fake_desktop.clipboard = "src/Main.java:3: error: cannot find symbol\n    System.out.printn(\"hi\");\n              ^\n1 error\n"
    t = await run(services, assistant, "Explain this compiler error")
    assert t.status is TaskStatus.COMPLETED, t.error
    data = t.result["data"]
    assert data["source"] == "the clipboard" and data["errors"][0]["line"] == 3
    assert "printn" in data["context"]
    assert "local model isn't available" in t.result["summary"]  # honest: no invented explanation


async def test_explain_error_with_nothing_to_explain_asks(services, assistant, fake_desktop):
    t = await run(services, assistant, "explain this error")
    assert t.status is TaskStatus.FAILED and "Copy the error text" in (t.error or "")


def _tiny_pdf(text: str) -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


async def test_summarize_front_document_writes_a_real_file(services, assistant, fake_desktop, tmp_path):
    doc = tmp_path / "release-notes.md"
    doc.write_text("# Release notes\n\nThe cursor relay now streams at 30 Hz. Sleep pauses the microphone and cursor streaming. "
                   "Research verifies TLS against the system trust store. The avatar shows only the head and shoulders.\n")
    fake_desktop.document = doc
    t = await run(services, assistant, "Summarize this document")
    assert t.status is TaskStatus.COMPLETED and t.verified, t.error
    out = Path(t.result["data"]["output"])
    assert out.exists() and "release-notes.md" in out.read_text()
    assert "no local model" in t.result["data"]["summaryKind"]


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")
async def test_summarize_pdf_by_path(services, assistant, tmp_path):
    pdf = tmp_path / "spec.pdf"
    pdf.write_bytes(_tiny_pdf("The pairing code expires after five minutes."))
    t = await run(services, assistant, f"summarize {pdf}")
    assert t.status is TaskStatus.COMPLETED, t.error
    assert "pairing code expires" in t.result["data"]["summary"]


@pytest.mark.skipif(not shutil.which("javac"), reason="no JDK")
async def test_create_java_class_and_test_it_with_a_real_jdk(services, assistant):
    t = await run(services, assistant, "Create a Java class and test it", timeout=120)
    assert t.status is TaskStatus.COMPLETED and t.verified, t.error
    data = t.result["data"]
    assert Path(data["files"][0]).exists() and data["exit"] == 0 and "All tests passed" in data["stdout"]
    assert "PASS greet() greets by name" in t.result["summary"]


async def test_java_without_a_compiler_is_reported(services, assistant, monkeypatch):
    monkeypatch.setattr(dev_tools, "_javac", lambda: (None, None))
    t = await run(services, assistant, "create a java class called Invoice")
    assert t.status is TaskStatus.FAILED and "brew install openjdk" in t.result["summary"]
    assert (services.workspace.root / "java" / "Invoice" / "Invoice.java").exists()


async def test_architecture_diagram_without_steps(services, assistant):
    t = await run(services, assistant, "Generate a diagram explaining the system architecture")
    assert t.status is TaskStatus.COMPLETED and t.verified, t.error
    svg = Path(t.result["data"]["path"])
    root = ET.fromstring(svg.read_text())  # valid XML
    texts = " ".join(e.text or "" for e in root.iter() if e.tag.endswith("text"))
    assert "Device hub" in texts and "Ollama" in texts and "whisper.cpp" in texts
    assert Path(t.result["data"]["mermaid"]).read_text().startswith("flowchart LR")


async def test_vague_diagram_without_model_asks_one_question(services, assistant):
    t = await run(services, assistant, "draw a diagram of my morning")
    assert t.status is TaskStatus.FAILED and "won't invent" in (t.error or "")
