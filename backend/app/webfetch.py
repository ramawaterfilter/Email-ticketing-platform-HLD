"""Fetch a public web page or document and return its readable text.

Used to import/refresh knowledge-base documents from a URL. Guards against SSRF:
only http(s), and every hop (including redirects) must resolve to a public IP.
No third-party dependency — urllib + html.parser only.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .errors import ApiError
from .llm import extract_text

USER_AGENT = "RamaSupportDesk-KB/1.0"
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 15
MAX_REDIRECTS = 5


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Return None so urllib raises HTTPError on 3xx; we follow redirects ourselves,
    # re-validating each target against the SSRF guard.
    def redirect_request(self, *args, **kwargs):  # noqa: D401, ANN001, ANN002, ANN003
        return None


def _validate(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ApiError(400, "bad_url", "Enter a web address starting with http:// or https://")
    host = parsed.hostname
    if not host:
        raise ApiError(400, "bad_url", "That URL has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port)
    except OSError:
        raise ApiError(400, "unreachable", "Could not resolve that web address")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ApiError(400, "blocked", "That address is not allowed")


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "br", "div", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}
    _SKIP = {"script", "style", "noscript", "head", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self._SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title and data.strip():
            self.title = data.strip()
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str | None, str]:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text).strip()
    return parser.title, text


def fetch_url_text(url: str) -> tuple[str | None, str]:
    """Return (title, extracted_text) for a public page/PDF/doc at ``url``."""
    current = url.strip()
    for _ in range(MAX_REDIRECTS):
        _validate(current)
        request = urllib.request.Request(
            current,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/pdf,text/*,*/*"},
        )
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            response = opener.open(request, timeout=TIMEOUT)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get("Location")
                if not location:
                    raise ApiError(400, "fetch_failed", "The page redirected without a destination")
                current = urljoin(current, location)
                continue
            raise ApiError(400, "fetch_failed", f"The page returned HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001
            raise ApiError(400, "fetch_failed", f"Could not fetch that page: {str(exc)[:150]}")

        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ApiError(400, "too_large", "That page is larger than 5 MB")
        ctype = response.headers.get("Content-Type", "").lower()
        is_html = "html" in ctype or (not ctype and b"<html" in data[:2000].lower())
        if is_html:
            title, text = html_to_text(data.decode("utf-8", "replace"))
        else:
            title, text = None, extract_text(current, ctype, data)
        if not text.strip():
            raise ApiError(400, "no_text", "No readable text could be extracted from that page")
        return title, text
    raise ApiError(400, "too_many_redirects", "That web address redirects too many times")
