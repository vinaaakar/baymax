import math

import pytest

from baymax.config import ResearchConfig
from baymax.documents import writer
from baymax.images.providers import ImageService, png_size, placeholder_svg
from baymax.config import ImageConfig
from baymax.research.report import ResearchReport, key_sentences
from baymax.research.web import FetchBlocked, Page, ResearchUnavailable, WebClient, _check_public, check_url, extract_text, parse_ddg
from baymax.simulation.sims import parse_params, pick_simulation, run_simulation

DDG = """
<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa&rut=x">Example <b>A</b></a>
<a class="result__snippet">Snippet about A.</a></div>
<div class="result"><a class="result__a" href="https://example.com/b">Example B</a><div class="result__snippet">B text</div></div>
<div class="result"><a class="result__a" href="javascript:alert(1)">bad</a></div>
"""


def test_parse_search_results():
    r = parse_ddg(DDG)
    assert [x.url for x in r] == ["https://example.org/a", "https://example.com/b"]
    assert r[0].title == "Example A" and r[0].snippet == "Snippet about A."


def test_extract_text_skips_scripts_and_keeps_title():
    html = ("<html><head><title> My Page </title><meta name='description' content='desc'></head><body><nav>menu</nav>"
            "<script>evil()</script><p>Hello <b>world</b>.</p><p>Second para.</p><footer>f</footer></body></html>")
    title, desc, text = extract_text(html)
    assert title == "My Page" and desc == "desc"
    assert "evil" not in text and "menu" not in text and "Hello world." in text


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x.org", "http://user:pw@example.com", "http:///nohost"])
def test_url_checks(url):
    with pytest.raises(FetchBlocked):
        check_url(url)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "10.0.0.1", "192.168.1.10", "169.254.169.254"])
def test_ssrf_guard(host):
    with pytest.raises(FetchBlocked):
        _check_public(host)


async def test_local_only_mode_blocks_network():
    c = WebClient(ResearchConfig(local_only=True))
    with pytest.raises(ResearchUnavailable):
        await c.search("anything")
    with pytest.raises(ResearchUnavailable):
        await c.fetch("https://example.com")


def test_report_preserves_citations_and_labels_inference(tmp_path):
    p1 = Page("https://a.example/x", "Source A", "Whisper.cpp runs speech recognition locally on Apple Silicon. It is fast.")
    p2 = Page("https://b.example/y", "Source B", "Some other text.")
    rep = ResearchReport("whisper speed", [p1, p2], {1: ["Whisper.cpp runs speech recognition locally on Apple Silicon."], 2: []})
    md = rep.to_markdown()
    assert "[1]" in md and "https://a.example/x" in md and "https://b.example/y" in md
    assert "No local language model was available" in md
    rep.analysis, rep.analysis_by = "Both agree [1]. Inference: it is probably fast.", "llama3.2:3b"
    md = rep.to_markdown()
    assert "llama3.2:3b" in md and "inference" in md.lower()
    mdp, jsp = rep.save(tmp_path, "r")
    assert mdp.exists() and jsp.exists()


def test_key_sentences_prefers_query_terms():
    text = ("The weather was nice. Apple Silicon has unified memory shared by CPU and GPU, which helps local models. "
            "Bananas are yellow and tasty fruits that grow in bunches. Local language models need memory bandwidth to run quickly.")
    out = key_sentences(text, "local models memory", 2)
    assert all("memory" in s for s in out)


# ------------------------------------------------------------- simulation
def test_projectile_matches_analytic():
    tel = run_simulation("projectile", {"speed": 20, "angle_deg": 45})
    assert tel.source == "simulated"
    assert abs(tel.summary["range_m"] - tel.summary["analytic_range_m"]) / tel.summary["analytic_range_m"] < 0.02


def test_pendulum_period_and_motor_and_drone():
    tel = run_simulation("pendulum", {"length": 1.0, "angle_deg": 5, "damping": 0.0, "duration": 4})
    ang = tel.series["angle_deg"]
    t = tel.series["t"]
    crossings = [t[i] for i in range(1, len(ang)) if ang[i - 1] > 0 >= ang[i]]
    assert abs((crossings[1] - crossings[0]) - 2 * math.pi * math.sqrt(1 / 9.81)) < 0.05
    m = run_simulation("dc_motor_pid", {"target_rpm": 1500})
    assert abs(m.summary["final_rpm"] - 1500) < 30
    d = run_simulation("drone_altitude", {"target_m": 10})
    assert abs(d.summary["final_altitude_m"] - 10) < 0.5
    with pytest.raises(ValueError):
        run_simulation("projectile", {"rm": 1})
    with pytest.raises(ValueError):
        run_simulation("warp_drive", {})


def test_simulation_parsing():
    assert pick_simulation("simulate a projectile launched at 30 m/s") == "projectile"
    assert pick_simulation("simulate the drone hover altitude") == "drone_altitude"
    assert parse_params("projectile", "at 30 m/s and 60 degrees") == {"speed": 30.0, "angle_deg": 60.0}
    assert parse_params("dc_motor_pid", "motor to 2000 rpm with kp=0.05") == {"target_rpm": 2000.0, "kp": 0.05}
    # limits are clamped
    tel = run_simulation("projectile", {"speed": 1e9, "angle_deg": 45})
    assert tel.params["speed"] == 2000.0


# --------------------------------------------------------------- documents
def test_documents_and_diagrams(tmp_path):
    assert writer.parse_steps("draw a flowchart of plan -> build -> test") == ["plan", "build", "test"]
    assert writer.parse_steps("1. design\n2. print\n3. assemble") == ["design", "print", "assemble"]
    svg = writer.flow_diagram_svg(["a", "b <script>"], "T")
    assert svg.startswith("<svg") and "<script>" not in svg
    chart = writer.line_chart_svg([0, 1, 2], {"y": [0, 1, 4]}, title="t", note="SIMULATED DATA")
    assert "SIMULATED DATA" in chart
    p = writer.write_csv(tmp_path / "d.csv", {"t": [0, 1], "x": [2, 3]})
    assert p.read_text().splitlines() == ["t,x", "0,2", "1,3"]
    md = writer.write_markdown(tmp_path / "doc.md", "Title", "Body", {"author": "BAYMAX"})
    assert md.read_text().startswith("---\nauthor: BAYMAX")
    with pytest.raises(ValueError):
        writer.write_svg(tmp_path / "x.svg", "<html>")


# ------------------------------------------------------------------ images
async def test_image_generation_unavailable_is_explicit(tmp_path):
    svc = ImageService(ImageConfig(provider="none"))
    st = await svc.status()
    assert not st["available"] and "No image generator" in st["message"]
    from baymax.images.providers import ImageUnavailable

    with pytest.raises(ImageUnavailable):
        await svc.generate("a cat", tmp_path)
    assert not list(tmp_path.iterdir())  # nothing written


def test_png_validation_and_placeholder_label():
    import struct
    import zlib

    def png(w, h):
        raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

    assert png_size(png(4, 3)) == (4, 3)
    with pytest.raises(ValueError):
        png_size(b"GIF89a....")
    assert "PLACEHOLDER" in placeholder_svg("hero image") and "Not a generated image" in placeholder_svg("x")
