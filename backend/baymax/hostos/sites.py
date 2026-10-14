"""Turn what the user called a website into a URL, separately from which app (browser) should open it.

"Open Gemini in Safari" = destination "Gemini" + app "Safari". The destination is a URL/domain, a well-known public
site name, or — when unknown — an explicit web search (reported as a search, never as the site itself).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse

# Well-known public sites by the name people say. Generic (not per-user), used only for "open <site>".
KNOWN_SITES = {
    "gemini": "https://gemini.google.com", "google gemini": "https://gemini.google.com", "chatgpt": "https://chatgpt.com",
    "chat gpt": "https://chatgpt.com", "claude": "https://claude.ai", "google": "https://www.google.com",
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com", "google docs": "https://docs.google.com", "google maps": "https://maps.google.com",
    "maps": "https://maps.google.com", "github": "https://github.com", "stack overflow": "https://stackoverflow.com",
    "stackoverflow": "https://stackoverflow.com", "wikipedia": "https://www.wikipedia.org", "reddit": "https://www.reddit.com",
    "linkedin": "https://www.linkedin.com", "twitter": "https://x.com", "x": "https://x.com", "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com", "amazon": "https://www.amazon.com", "netflix": "https://www.netflix.com",
    "notion": "https://www.notion.so", "figma": "https://www.figma.com", "outlook": "https://outlook.live.com",
    "spotify web": "https://open.spotify.com", "hacker news": "https://news.ycombinator.com", "perplexity": "https://www.perplexity.ai",
}
BROWSERS = ("Safari", "Google Chrome", "Firefox", "Arc", "Brave Browser", "Microsoft Edge", "Opera", "Chromium", "Vivaldi")
_DOMAIN = re.compile(r"^(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:/\S*)?$", re.I)


@dataclass
class Destination:
    url: str
    kind: str  # "url" | "site" | "search"
    label: str

    @property
    def host(self) -> str:
        return (urlparse(self.url).hostname or "").removeprefix("www.")


def resolve_destination(text: str) -> Destination | None:
    t = re.sub(r"^(the |a )?(website |site |page )?", "", text.strip().strip("\"'“”").rstrip(".!?"), flags=re.I)
    t = re.sub(r"\s+(website|site|web ?page|page|homepage)$", "", t, flags=re.I).strip()
    if not t:
        return None
    if _DOMAIN.match(t):
        url = t if re.match(r"^https?://", t, re.I) else "https://" + t
        return Destination(url, "url", t)
    key = t.lower()
    if key in KNOWN_SITES:
        return Destination(KNOWN_SITES[key], "site", t)
    return Destination(f"https://duckduckgo.com/?q={quote_plus(t)}", "search", t)


def same_site(actual_url: str, dest: Destination) -> bool:
    """Did the browser land on the requested site? The requested host or one of its subdomains counts
    (en.wikipedia.org for wikipedia.org); a sibling on the same parent domain does not: www.google.com/search is not
    gemini.google.com, so it must never verify "Gemini" or receive a prompt meant for it."""
    host = (urlparse(actual_url).hostname or "").removeprefix("www.")
    want = dest.host
    if not host or not want:
        return False
    return host == want or host.endswith("." + want)


_BROWSER_WORDS = {"safari": "Safari", "chrome": "Google Chrome", "google chrome": "Google Chrome", "firefox": "Firefox", "arc": "Arc",
                  "brave": "Brave Browser", "edge": "Microsoft Edge", "opera": "Opera", "vivaldi": "Vivaldi"}


def browser_named_in(text: str) -> str | None:
    """'open chatgpt on the safari' -> 'Safari' (the browser the user actually said, if any)."""
    t = text.lower()
    for word in sorted(_BROWSER_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(word)}\b", t):
            return _BROWSER_WORDS[word]
    return None


def ground_destination(user_text: str, proposed: str) -> str | None:
    """Check a website the *model* proposed against what the user said. Returns what to open, or None when the
    proposal isn't grounded in the request (seen: "open ChatGPT on the Safari" -> the model invented chatgpt.ai).

    - the proposed domain/name appears in the user's words: keep it;
    - the user named a well-known site: open that site (its canonical address), whatever the model wrote;
    - otherwise: None (ask instead of opening a made-up address)."""
    t = " ".join(user_text.lower().split())
    dest = resolve_destination(proposed)
    if dest is None:
        return None
    named = [k for k in sorted(KNOWN_SITES, key=len, reverse=True) if len(k) > 1 and re.search(rf"\b{re.escape(k)}\b", t)]
    if dest.kind == "url" and (dest.host and dest.host in t or dest.label.lower() in t):
        return proposed
    if named:
        return named[0]  # resolve_destination(name) -> the real site
    if dest.kind != "url" and dest.label.lower() in t:
        return proposed
    return None
