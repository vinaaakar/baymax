"""Audit defects D2, D3, D4: research as a verifiable two-step plan; no typing into an unverified page; no success
reported for an unverified destination. Simulated: Ollama and the macOS desktop (FakeDesktop, whose browser address
bar is readable only with `automation=True`). Real: router, assistant, planner, policy/approvals, tools.
"""

from __future__ import annotations

import asyncio

from baymax.tasks.models import PlanStep, TaskStatus
from test_capability_gaps import env, model  # noqa: F401  (fixtures)

GEMINI = "https://gemini.google.com"
# Any page with a wide text box: the delegate's fallback field picker would accept it.
OTHER_PAGE_WITH_A_FIELD = "AXTextField\tSearch\t\t\t200\t120\t600\t30"


async def _drain(sub, wait=0.3):
    out = []
    while True:
        try:
            out.append(await asyncio.wait_for(sub.get(), wait))
        except asyncio.TimeoutError:
            return out


async def _run(env, tool, args, timeout=10):
    task = env["s"].tasks.submit("t", "t", "t", [PlanStep(tool, tool, args)])
    return await env["s"].tasks.wait(task.id, timeout)


# ------------------------------------------------------------------ D2: open the site, then web research
async def test_open_gemini_in_safari_and_research_runs_the_two_step_plan_without_typing_or_approval(env):
    sub = env["s"].bus.subscribe()
    r = await env["a"].handle_text("Open Gemini in Safari and research how modern AI works.", source="device:iphone", voice=True)
    t = await env["s"].tasks.wait(r["taskId"], 10)
    assert [(p.tool, p.args) for p in t.plan] == [("apps.open_url", {"url": "Gemini", "browser": "Safari"}),
                                                 ("research.web", {"query": "how modern AI works", "max_sources": 4})]
    assert ("open_url", GEMINI, "Safari") in env["desk"].calls
    assert not [c for c in env["desk"].calls if c[0] in ("type", "focus", "keys", "return")]  # nothing typed into Gemini
    events = await _drain(sub)
    assert "approval.request" not in [e.type for e in events]  # web research needs no remote approval
    # The test env is local-only (no network): the research step must fail honestly, never claim results.
    assert t.status is TaskStatus.FAILED and "No online research was performed" in (t.error or "")


async def test_the_research_step_never_runs_when_the_site_could_not_be_opened(env):
    env["desk"].redirects[GEMINI] = "https://example.com/blocked"
    r = await env["a"].handle_text("Open Gemini in Safari and research how AI works")
    t = await env["s"].tasks.wait(r["taskId"], 10)
    assert t.status is TaskStatus.FAILED and "showing https://example.com/blocked" in (t.error or "")
    assert t.result["data"].get("errorKind") == "not_verified"  # failed at step 1; research.web never started


def test_explicit_ask_keeps_the_delegate_workflow():
    from baymax.creation3d.classifier import RequestClassifier
    from baymax.creation3d.registry import GeneratorRegistry
    from baymax.intent.router import IntentRouter

    i = IntentRouter(RequestClassifier(GeneratorRegistry())).route("Ask Gemini to research how AI works")
    assert i.tool == "ai.delegate" and i.args["service"] == "gemini" and i.args["submit"] is True


# ------------------------------------------------------------------ D3: never type into an unverified page
async def test_delegate_stops_before_focusing_when_the_browser_shows_another_page(env):
    desk = env["desk"]
    desk.redirects[GEMINI] = "https://www.google.com/search?q=gemini"
    desk.ax_dump["Safari"] = OTHER_PAGE_WITH_A_FIELD
    t = await _run(env, "ai.delegate", {"service": "gemini", "prompt": "hi", "via": "browser", "browser": "Safari"})
    s = t.result["summary"]
    assert t.status is TaskStatus.FAILED and t.result["data"]["errorKind"] == "not_verified"
    assert "showing https://www.google.com/search?q=gemini" in s and "Nothing was typed or sent" in s
    assert not [c for c in desk.calls if c[0] in ("ax", "focus", "type", "keys", "return")]  # the page was never even searched


async def test_delegate_stops_when_the_address_bar_cannot_be_read(env):
    desk = env["desk"]
    desk.automation = False  # Automation permission for Safari not granted
    desk.ax_dump["Safari"] = OTHER_PAGE_WITH_A_FIELD
    t = await _run(env, "ai.delegate", {"service": "gemini", "prompt": "hi", "via": "browser", "browser": "Safari"})
    assert t.status is TaskStatus.FAILED and "Automation" in t.result["summary"]
    assert not [c for c in desk.calls if c[0] in ("focus", "type")]


async def test_delegate_still_types_when_the_page_is_verified(env):
    desk = env["desk"]
    desk.ax_dump["Safari"] = "AXTextArea\t\tEnter a prompt here\t\t300\t700\t600\t40"
    t = await _run(env, "ai.delegate", {"service": "gemini", "prompt": "hi there", "via": "browser", "browser": "Safari"})
    assert t.status is TaskStatus.COMPLETED and t.result["data"]["url"] == GEMINI
    assert ("type", "Safari", "hi there") in desk.calls


# ------------------------------------------------------------------ D4: unverified destinations are never "verified success"
async def test_open_url_on_the_wrong_page_is_a_failure(env):
    env["desk"].redirects[GEMINI] = "https://example.com/elsewhere"
    t = await _run(env, "apps.open_url", {"url": "Gemini", "browser": "Safari"})
    assert t.status is TaskStatus.FAILED and not t.verified
    assert t.result["outcome"] == "failed" and t.result["data"]["errorKind"] == "not_verified"
    assert "showing https://example.com/elsewhere" in t.result["summary"]


async def test_open_url_with_an_unreadable_address_bar_is_dispatched_unverified(env):
    env["desk"].automation = False
    t = await _run(env, "apps.open_url", {"url": "Gemini", "browser": "Safari"})
    assert t.status is TaskStatus.COMPLETED and not t.verified and t.result["outcome"] == "dispatched_unverified"
    assert t.result["summary"].startswith("Asked Safari") and "couldn't confirm" in t.result["summary"]


async def test_open_url_in_the_default_browser_never_claims_it_opened(env):
    t = await _run(env, "apps.open_url", {"url": "Gemini"})
    s = t.result["summary"]
    assert not t.verified and t.result["outcome"] == "dispatched_unverified"
    assert s.startswith("Asked your default browser") and "isn't confirmed" in s and not s.startswith("Opened")


async def test_open_url_verified_only_when_the_address_bar_matches(env):
    t = await _run(env, "apps.open_url", {"url": "Gemini", "browser": "Safari"})
    assert t.status is TaskStatus.COMPLETED and t.verified and t.result["outcome"] == "completed_verified"


async def test_remote_high_risk_approval_stays_disabled(env):
    assert env["s"].settings.security.remote_high_risk_approval is False
    sub = env["s"].bus.subscribe()
    task = env["s"].tasks.submit("d", "d", "d", [PlanStep("d", "ai.delegate", {"service": "gemini", "prompt": "x", "submit": True})],
                                 source="device:iphone")
    req = next(e for e in await _drain(sub) if e.type == "approval.request")
    assert req.payload["remoteAllowed"] is False
    ok, why = env["s"].tasks.respond_approval(req.payload["approvalId"], True, "device:iphone")
    assert not ok and "Mac" in why
    env["s"].tasks.respond_approval(req.payload["approvalId"], False, "local")  # deny on the Mac: nothing runs
    t = await env["s"].tasks.wait(task.id, 5)
    assert t.status is TaskStatus.CANCELLED and not [c for c in env["desk"].calls if c[0] in ("open_url", "type")]
