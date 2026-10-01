"""Fetching pages from a lead's website. The only module that requests lead-supplied URLs."""

import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

MAX_BYTES = 1_000_000
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 10
USER_AGENT = "leadqual/0.1 (lead research prototype)"

Resolver = Callable[[str], list[str]]


class FetchError(Exception):
    """The page could not, or must not, be fetched."""


class HostNotFound(FetchError):
    """The host name does not resolve: there is no such website."""


@dataclass(frozen=True)
class Page:
    url: str  # after redirects
    text: str
    links: tuple[str, ...]  # absolute URLs on the same host

    @property
    def host(self) -> str:
        return bare_host(self.url)


def bare_host(url: str) -> str:
    """The host of a URL without 'www.'; empty for anything that is not a parseable URL."""
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def resolve_host(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def check_public_url(url: str, resolve: Resolver = resolve_host) -> None:
    """Refuse anything but plain web URLs on public addresses.

    The URL comes from a stranger's form submission, so without this check the
    fetcher could be pointed at internal services (server-side request forgery).
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise FetchError(f"not a web URL: {url}") from error
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError(f"not a web URL: {url}")
    if port not in (None, 80, 443):
        raise FetchError(f"unusual port refused: {url}")
    try:
        addresses = resolve(parts.hostname)
    except (OSError, UnicodeError) as error:  # UnicodeError: a host name DNS cannot encode
        raise HostNotFound(f"cannot resolve {parts.hostname}") from error
    if not addresses or not all(ipaddress.ip_address(address).is_global for address in addresses):
        raise FetchError(f"{parts.hostname} is not a public address")


def fetch_page(
    url: str, *, client: httpx.Client | None = None, resolve: Resolver = resolve_host
) -> Page:
    """Download one HTML page, re-checking every redirect hop."""
    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
    try:
        for _ in range(MAX_REDIRECTS + 1):
            check_public_url(url, resolve)
            status, headers, body = _get(client, url)
            if status in (301, 302, 303, 307, 308) and "location" in headers:
                try:
                    url = urljoin(url, headers["location"])
                except ValueError as error:
                    raise FetchError(f"malformed redirect from {url}") from error
                continue
            if status != 200:
                raise FetchError(f"HTTP {status} from {url}")
            if "html" not in headers.get("content-type", ""):
                raise FetchError(f"not an HTML page: {url}")
            return parse_page(url, body)
        raise FetchError(f"too many redirects from {url}")
    finally:
        if owns_client:
            client.close()


def _get(client: httpx.Client, url: str) -> tuple[int, httpx.Headers, str]:
    try:
        with client.stream("GET", url, follow_redirects=False) as response:
            chunks = []
            size = 0
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= MAX_BYTES:
                    break
            encoding = response.encoding or "utf-8"
            body = b"".join(chunks)[:MAX_BYTES].decode(encoding, errors="replace")
            return response.status_code, response.headers, body
    except (httpx.HTTPError, httpx.InvalidURL) as error:  # InvalidURL is not an HTTPError
        raise FetchError(f"request to {url!r} failed: {type(error).__name__}") from error


def parse_page(url: str, html: str) -> Page:
    soup = BeautifulSoup(html, "html.parser")
    host = bare_host(url)
    links = []
    for anchor in soup.find_all("a", href=True):
        try:
            target = urljoin(url, anchor["href"]).split("#")[0]
        except ValueError:  # one malformed link must not cost us the whole page
            continue
        if bare_host(target) == host and target not in links:
            links.append(target)
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    return Page(url=url, text=text, links=tuple(links))
