"""In-process Accessibility reader (baymax.hostos.ax_inprocess) against a fake AX tree with the same calls as PyObjC's
ApplicationServices. Real-Mac behaviour (1983 ChatGPT elements in 0.17 s) is checked by tests_macos, not here."""

from __future__ import annotations

from types import SimpleNamespace

from baymax.hostos import ax_inprocess as ax
from baymax.hostos.desktop import parse_window_header
from baymax.screen.elements import parse_ax_dump


class El:
    def __init__(self, role, x=0, y=0, w=100, h=20, children=(), **attrs):
        self.a = {"AXRole": role, "AXPosition": ("point", x, y), "AXSize": ("size", w, h),
                  "AXChildren": list(children), **attrs}


class FakeAS:
    kAXValueCGPointType, kAXValueCGSizeType = "point", "size"

    def __init__(self, app):
        self.app, self.focused = app, None

    def AXUIElementCopyAttributeValue(self, el, name, _):
        if el is None or name not in el.a:
            return -25212, None
        return 0, el.a[name]

    def AXValueGetValue(self, v, kind, _):
        if v[0] != kind:
            return False, None
        return True, (SimpleNamespace(x=v[1], y=v[2]) if kind == "point" else SimpleNamespace(width=v[1], height=v[2]))

    def AXUIElementSetAttributeValue(self, el, name, value):
        if name == "AXFocused" and el.a.get("AXRole") in ax.TEXT_INPUTS:
            el.a["AXFocused"] = True
            return 0
        return -25205


def _chatgpt_like(n_filler=1980):
    """A window with ~2000 labelled elements and the message box at the very end (as in ChatGPT's web window)."""
    filler = [El("AXStaticText", 10, 10 + i, AXValue=f"line {i}") for i in range(n_filler)]
    box = El("AXTextArea", 300, 700, 600, 40, AXDescription="Chat with ChatGPT", AXValue="")
    web = El("AXWebArea", 0, 0, 1000, 800, children=[El("AXGroup", 0, 0, 1000, 800, children=filler + [box])])
    win = El("AXWindow", 0, 0, 1000, 800, children=[web], AXTitle="JARVIS Data Capabilities", AXSubrole="AXStandardWindow")
    app = El("AXApplication", AXMainWindow=win, AXWindows=[win],
             AXMenuBar=El("AXMenuBar", children=[El("AXMenuBarItem", 0, 0, 40, 20, AXTitle="Window")]))
    return app, box


def test_dump_reads_the_whole_window_and_never_drops_the_message_box():
    app, _ = _chatgpt_like()
    out = ax.dump(FakeAS(app), app, limit=400)
    head = parse_window_header(out)
    assert head == {"windows": 1, "chosen": "main", "name": "JARVIS Data Capabilities", "subrole": "AXStandardWindow",
                    "truncated": False}
    els = parse_ax_dump(out, "ChatGPT")
    boxes = [e for e in els if e.role == "AXTextArea"]
    assert len(boxes) == 1 and boxes[0].label == "Chat with ChatGPT" and (boxes[0].x, boxes[0].y) == (300, 700)
    assert 390 < sum(e.role == "AXStaticText" for e in els) <= 400  # other elements still respect the limit
    assert any(e.role == "AXMenuBarItem" and e.label == "Window" for e in els)


def test_unlabelled_box_uses_its_placeholder():
    box = El("AXTextArea", 5, 5, 600, 40, AXPlaceholderValue="Message ChatGPT")
    win = El("AXWindow", children=[box], AXTitle="w")
    app = El("AXApplication", AXMainWindow=win, AXWindows=[win])
    els = parse_ax_dump(ax.dump(FakeAS(app), app, limit=10), "ChatGPT")
    assert [e.label for e in els if e.role == "AXTextArea"] == ["Message ChatGPT"]


def test_no_visible_window_reports_zero_windows():
    app = El("AXApplication", AXWindows=[])
    assert parse_window_header(ax.dump(FakeAS(app), app, limit=10))["windows"] == 0


def test_walk_is_bounded_and_says_so():
    app, _ = _chatgpt_like()
    head = parse_window_header(ax.dump(FakeAS(app), app, limit=400, max_nodes=100))
    assert head["truncated"] is True


def test_find_focus_and_read_back_the_same_element():
    app, box = _chatgpt_like()
    AS = FakeAS(app)
    el = ax.find(AS, app, "AXTextArea", 302, 701)  # within 6 points of the frame origin
    assert el is box and ax.focus(AS, el) is True
    box.a["AXValue"] = "hello\nworld"
    assert ax.value_text(AS, el) == "hello\nworld"  # exact text, line breaks kept
    assert ax.find(AS, app, "AXTextArea", 320, 720) is None  # wrong position: not that element
    assert ax.find(AS, app, "AXTextField", 300, 700) is None  # wrong role


def test_focus_is_refused_for_non_inputs():
    app, _ = _chatgpt_like()
    AS = FakeAS(app)
    text = ax.find(AS, app, "AXStaticText", 10, 10)
    assert ax.focus(AS, text) is False


def test_frames_also_parse_from_the_axvalue_description():
    class Opaque:
        def __init__(self, s):
            self.s = s

        def __str__(self):
            return self.s

    class AS(FakeAS):
        def AXValueGetValue(self, v, kind, _):
            raise TypeError("unsupported")

    el = El("AXButton")
    el.a["AXPosition"] = Opaque("<AXValue 0x1 {value = x:12.5 y:30.0 type = kAXValueCGPointType}>")
    el.a["AXSize"] = Opaque("<AXValue 0x2 {value = w:80.0 h:22.0 type = kAXValueCGSizeType}>")
    assert ax.frame(AS(None), el) == (12.5, 30.0, 80.0, 22.0)
