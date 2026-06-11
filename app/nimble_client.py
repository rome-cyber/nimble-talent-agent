"""
Thin wrapper around Nimble Search and Extract APIs.
Matches the pattern used in nimble-lead-enrichment and nimble-competitor-monitor.
"""

from __future__ import annotations

import os
import re
import requests
from html.parser import HTMLParser

SEARCH_URL = "https://sdk.nimbleway.com/v1/search"
EXTRACT_URL = "https://sdk.nimbleway.com/v1/extract"


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "head", "noscript", "iframe"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "br", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self._chunks: list = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._depth += 1
        elif tag in self.BLOCK and self._chunks and self._chunks[-1] != "\n":
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data):
        if self._depth:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def get_text(self) -> str:
        joined = " ".join(self._chunks)
        text = re.sub(r"[ \t]+", " ", joined)
        text = re.sub(r"\n{3,}", "\n\n", text)
        lines = [l for l in text.splitlines() if len(l.strip()) > 2]
        return "\n".join(lines).strip()


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        return parser.get_text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html).strip()


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('NIMBLE_API_KEY')}",
        "Content-Type": "application/json",
    }


def search(query: str, focus: str = "general", num_results: int = 10) -> list:
    payload = {
        "query": query,
        "num_results": num_results,
        "search_depth": "lite",
        "focus": focus,
    }
    try:
        resp = requests.post(SEARCH_URL, headers=_headers(), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        print(f"  [nimble.search] {query[:60]}: {e}")
        return []


def extract(url: str) -> str:
    payload = {"url": url, "render": True, "driver": "vx8"}
    try:
        resp = requests.post(EXTRACT_URL, headers=_headers(), json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        raw = data.get("markdown") or data.get("text") or data.get("html", "")
        if not raw:
            return ""
        text = _html_to_text(raw) if raw.lstrip().startswith("<") else raw
        return text[:3000]
    except Exception as e:
        print(f"  [nimble.extract] {url}: {e}")
        return ""
