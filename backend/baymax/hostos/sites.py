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
    """Did the browser land on the requested site? (redirects within the same registrable domain count)."""
    host = (urlparse(actual_url).hostname or "").removeprefix("www.")
    want = dest.host
    if not host or not want:
        return False
    base = ".".join(want.split(".")[-2:])
    return host == want or host.endswith("." + base) or host == base
