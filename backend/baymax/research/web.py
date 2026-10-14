"""Web search and page retrieval.

* Search providers: DuckDuckGo HTML endpoint or a self-hosted SearXNG.
* Fetching: http(s) only, no credentials in URLs, private/loopback/link-local
  addresses blocked (SSRF guard), size and time bounded, HTML/text only.
* All retrieved content is untrusted data.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

import aiohttp

from ..config import ResearchConfig

USER_AGENT = "BAYMAX-research/0.1 (+local assistant)"


class ResearchUnavailable(Exception):
    pass


class FetchBlocked(Exception):
    pass


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class Page:
    url: str
    title: str
    text: str
    description: str = ""
    retrieved_at: float = field(default_factory=time.time)
    status: int = 200

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "title": self.title, "description": self.description, "retrievedAt": self.retrieved_at,
                "chars": len(self.text)}


class _DDGParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[SearchResult] = []
        self._in_a = self._in_snip = False
        self._href = ""
        self._title: list[str] = []
        self._snip: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        if tag == "a" and "result__a" in cls:
            self._in_a, self._href, self._title = True, a.get("href", ""), []
        elif tag in ("a", "div", "td") and "result__snippet" in cls:
            self._in_snip, self._snip = True, []

    def handle_endtag(self, tag):
        if self._in_a and tag == "a":
            self._in_a = False
            self.results.append(SearchResult(" ".join("".join(self._title).split()), _ddg_target(self._href), ""))
        elif self._in_snip and tag in ("a", "div", "td"):
            self._in_snip = False
            if self.results:
                self.results[-1].snippet = " ".join("".join(self._snip).split())

    def handle_data(self, data):
        if self._in_a:
            self._title.append(data)
        elif self._in_snip:
            self._snip.append(data)


def _ddg_target(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        q = parse_qs(parsed.query).get("uddg")
        if q:
            return q[0]
    return href


def parse_ddg(html_text: str) -> list[SearchResult]:
    p = _DDGParser()
    p.feed(html_text)
    return [r for r in p.results if r.url.startswith(("http://", "https://"))]


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "template", "iframe"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "section", "article", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False
        self.description = ""

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            a = dict(attrs)
            if (a.get("name") or a.get("property") or "").lower() in ("description", "og:description") and a.get("content"):
                self.description = self.description or a["content"].strip()
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


def extract_text(html_text: str) -> tuple[str, str, str]:
    p = _TextExtractor()
    try:
        p.feed(html_text)
    except Exception:  # malformed HTML: keep what we have
        pass
    lines = [" ".join(line.split()) for line in "".join(p.parts).splitlines()]
    text = "\n".join(line for line in lines if len(line) > 1)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return " ".join(p.title.split()), html.unescape(p.description), text


def _check_public(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchBlocked(f"cannot resolve {host}: {exc}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise FetchBlocked(f"refusing to fetch non-public address {ip} ({host})")


def check_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchBlocked("only http(s) URLs can be fetched")
    if parsed.username or parsed.password:
        raise FetchBlocked("URLs with credentials are not fetched")
    if not parsed.hostname:
        raise FetchBlocked("URL has no host")
    return parsed.hostname


class WebClient:
    def __init__(self, config: ResearchConfig) -> None:
        self.config = config
        self._session: aiohttp.ClientSession | None = None

    def _ensure_enabled(self) -> None:
        if self.config.local_only:
            raise ResearchUnavailable("local-only mode is on: web research is disabled (Settings → Network)")

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}, trust_env=True)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def search(self, query: str, limit: int | None = None) -> list[SearchResult]:
        self._ensure_enabled()
        limit = limit or self.config.max_results
        http = await self._http()
        timeout = aiohttp.ClientTimeout(total=self.config.fetch_timeout_s)
        try:
            if self.config.search_provider == "searxng":
                if not self.config.searxng_url:
                    raise ResearchUnavailable("SearXNG selected but research.searxng_url is not configured")
                async with http.get(f"{self.config.searxng_url.rstrip('/')}/search?q={quote_plus(query)}&format=json", timeout=timeout) as r:
                    if r.status != 200:
                        raise ResearchUnavailable(f"SearXNG returned HTTP {r.status}")
                    data = await r.json()
                    return [SearchResult(x.get("title", ""), x.get("url", ""), x.get("content", "")) for x in data.get("results", [])][:limit]
            async with http.post("https://html.duckduckgo.com/html/", data={"q": query}, timeout=timeout) as r:
                if r.status != 200:
                    raise ResearchUnavailable(f"search provider returned HTTP {r.status}")
                return parse_ddg(await r.text())[:limit]
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise ResearchUnavailable(f"web search failed ({exc.__class__.__name__}); check the network connection") from None

    async def fetch(self, url: str) -> Page:
        self._ensure_enabled()
        host = check_url(url)
        await asyncio.get_running_loop().run_in_executor(None, _check_public, host)
        http = await self._http()
        timeout = aiohttp.ClientTimeout(total=self.config.fetch_timeout_s)
        try:
            async with http.get(url, timeout=timeout, allow_redirects=True, max_redirects=5) as r:
                final_host = r.url.host or ""
                if final_host != host:
                    await asyncio.get_running_loop().run_in_executor(None, _check_public, final_host)
                ctype = r.headers.get("Content-Type", "")
                if not any(t in ctype for t in ("text/html", "text/plain", "application/xhtml")):
                    raise FetchBlocked(f"unsupported content type {ctype or 'unknown'}")
                raw = await r.content.read(self.config.max_page_bytes + 1)
                text_raw = raw[: self.config.max_page_bytes].decode(r.charset or "utf-8", errors="replace")
                if "text/plain" in ctype:
                    title, desc, text = url, "", text_raw
                else:
                    title, desc, text = extract_text(text_raw)
                return Page(str(r.url), title or url, text[:200_000], desc, status=r.status)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise ResearchUnavailable(f"could not fetch {url}: {exc.__class__.__name__}") from None
