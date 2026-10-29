"""Bounded official-document retrieval with validated, pinned public destinations.

This module never uses environment proxies, follows an unvalidated redirect,
executes PDF actions, or writes retrieved documents to shared storage.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import time
from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from pypdf import PdfReader, apply_configuration

from research.reporting.live_report import public_source_url, redact_private_locations

MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_TEXT_CHARACTERS = 20_000
MAX_PDF_PAGES = 30
MAX_DECODED_STREAM_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
SOURCE_TIMEOUT_SECONDS = 10
OFFICIAL_DOMAINS = ("sec.gov", "nseindia.com", "bseindia.com")


class SourceRejected(ValueError):
    """A deliberately bounded, safe message for a rejected source."""


def _hostname(value: str) -> str:
    try:
        hostname = value.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, AttributeError) as exc:
        raise SourceRejected("A valid official website hostname is required.") from exc
    if len(hostname) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", hostname):
        raise SourceRejected("A valid official website hostname is required.")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise SourceRejected("Official source URLs must use a website hostname, not an IP address.")
    if "." not in hostname or hostname.endswith((".local", ".localhost", ".internal", ".lan")):
        raise SourceRejected("Private and local source locations are not allowed.")
    if any(
        not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
        for label in hostname.split(".")
    ):
        raise SourceRejected("A valid official website hostname is required.")
    return hostname


def _allowed_domains(company_website: str | None) -> tuple[str, ...]:
    domains = list(OFFICIAL_DOMAINS)
    if company_website:
        try:
            website = urlsplit(company_website)
            if (
                website.scheme in {"http", "https"}
                and website.hostname
                and not website.username
                and not website.password
            ):
                company_host = _hostname(website.hostname)
                domains.append(company_host.removeprefix("www."))
        except (SourceRejected, ValueError):
            pass
    return tuple(dict.fromkeys(domains))


def _validated_target(url: str, domains: tuple[str, ...]) -> tuple[str, str, list[str]]:
    if not isinstance(url, str) or len(url) > 2048 or re.search(r"[\x00-\x20\x7f\\]", url):
        raise SourceRejected(
            "Use an HTTPS link from the company's official website, SEC, NSE, or BSE."
        )
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise SourceRejected(
                "Use an HTTPS official-source URL without credentials or a custom port."
            )
        host = _hostname(parsed.hostname)
    except ValueError as exc:
        if isinstance(exc, SourceRejected):
            raise
        raise SourceRejected("Use a valid HTTPS official-source URL.") from exc
    if not any(host == domain or host.endswith("." + domain) for domain in domains):
        raise SourceRejected(
            "The source must belong to the company's official website, SEC, NSE, or BSE."
        )
    records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    addresses = []
    for record in records:
        address = ipaddress.ip_address(record[4][0])
        mapped = getattr(address, "ipv4_mapped", None)
        inspected = mapped or address
        if (
            not inspected.is_global
            or inspected.is_multicast
            or inspected.is_reserved
            or getattr(inspected, "is_site_local", False)
        ):
            raise SourceRejected(
                "The official hostname did not resolve exclusively to public network addresses."
            )
        addresses.append(str(address))
    if not addresses:
        raise SourceRejected("The official hostname did not return a usable public address.")
    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    normalized = urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
    return normalized, target, list(dict.fromkeys(addresses))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str, timeout: float):
        super().__init__(hostname, port=443, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        # The TCP connection uses the already validated address. TLS SNI and
        # hostname verification still use the original official website name.
        connection = socket.create_connection((self._address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
        except Exception:
            connection.close()
            raise


def _download(url: str, domains: tuple[str, ...]) -> tuple[bytes, str, str]:
    deadline = time.monotonic() + SOURCE_TIMEOUT_SECONDS
    for redirect in range(MAX_REDIRECTS + 1):
        normalized, target, addresses = _validated_target(url, domains)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceRejected(
                "The official source timed out. Try another public disclosure link."
            )
        host = urlsplit(normalized).hostname
        connection = _PinnedHTTPSConnection(host, addresses[0], timeout=remaining)
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "User-Agent": "AstraResearch/1.0 public-document-review",
                    "Accept": "text/html,text/plain,application/pdf,application/xhtml+xml",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if redirect >= MAX_REDIRECTS or not location:
                    raise SourceRejected(
                        "The official source redirected too many times or omitted its destination."
                    )
                url = urljoin(normalized, location)
                continue
            if not 200 <= response.status < 300:
                raise SourceRejected("The official source did not return an accessible document.")
            if (response.getheader("Content-Encoding") or "identity").lower().strip() not in {
                "",
                "identity",
            }:
                raise SourceRejected(
                    "Compressed source responses are not accepted for bounded document retrieval."
                )
            length = response.getheader("Content-Length")
            if length and (not length.isdecimal() or int(length) > MAX_DOCUMENT_BYTES):
                raise SourceRejected("The document exceeds the 1 MiB retrieval limit.")
            chunks, total = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceRejected(
                        "The official source timed out. Try another public disclosure link."
                    )
                if connection.sock:
                    connection.sock.settimeout(remaining)
                chunk = response.read(min(64 * 1024, MAX_DOCUMENT_BYTES + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOCUMENT_BYTES:
                    raise SourceRejected("The document exceeds the 1 MiB retrieval limit.")
                chunks.append(chunk)
            return b"".join(chunks), (response.getheader("Content-Type") or "").lower(), normalized
        finally:
            connection.close()
    raise SourceRejected("The official source could not be retrieved safely.")


def _clean_text(value: str) -> str:
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return redact_private_locations(re.sub(r"[ \t]+", " ", value)).strip()


def _extract(content: bytes, content_type: str) -> tuple[str, str]:
    media_type = content_type.split(";", 1)[0].strip()
    if content.startswith(b"%PDF-") or media_type == "application/pdf":
        with apply_configuration(
            maximum_declared_stream_length=MAX_DECODED_STREAM_BYTES,
            array_based_stream_maximum_output_length=MAX_DECODED_STREAM_BYTES,
            zlib_maximum_output_length=MAX_DECODED_STREAM_BYTES,
            lzw_maximum_output_length=MAX_DECODED_STREAM_BYTES,
            run_length_maximum_output_length=MAX_DECODED_STREAM_BYTES,
            stream_decoding_work_maximum_length=MAX_DECODED_STREAM_BYTES * 4,
            page_tree_maximum_entries=1000,
            page_tree_maximum_depth=30,
            xform_maximum_invocations_per_extraction=100,
        ):
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise SourceRejected("Encrypted PDFs cannot be used as public research evidence.")
            if len(reader.pages) > MAX_PDF_PAGES:
                raise SourceRejected(
                    "The PDF exceeds the 30-page retrieval limit. Use a smaller official disclosure."
                )
            text, characters = [], 0
            for number, page in enumerate(reader.pages, 1):
                part = _clean_text(page.extract_text() or "")
                if part:
                    text.append(f"[Page {number}]\n" + part[: MAX_TEXT_CHARACTERS - characters])
                    characters += len(text[-1])
                if characters >= MAX_TEXT_CHARACTERS:
                    break
            return "Official PDF disclosure", "\n\n".join(text)[:MAX_TEXT_CHARACTERS]
    if media_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
        raise SourceRejected("Only public HTML, text, and PDF disclosures are supported.")
    decoded = content.decode("utf-8", errors="replace")
    if media_type == "text/plain":
        return "Official text disclosure", _clean_text(decoded)[:MAX_TEXT_CHARACTERS]
    soup = BeautifulSoup(decoded, "html.parser")
    title = (
        _clean_text(soup.title.get_text(" ", strip=True))[:160]
        if soup.title
        else "Official company disclosure"
    )
    for item in soup.find_all(
        ["script", "style", "noscript", "svg", "iframe", "object", "template", "nav", "footer"]
    ):
        item.decompose()
    return title, _clean_text(soup.get_text("\n", strip=True))[:MAX_TEXT_CHARACTERS]


def fetch_official_sources(urls: list[str], company_website: str | None) -> list[dict]:
    """Retrieve at most three bounded, official disclosures without persisting raw data."""
    if not isinstance(urls, list) or len(urls) > 3:
        raise ValueError("Provide at most three official-source URLs.")
    domains, results = _allowed_domains(company_website), []
    for url in urls:
        public_url = public_source_url(url)
        result = {
            "title": "Official source unavailable",
            "url": public_url or "",
            "content": "",
            "warning": None,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "provider": "official-document-retrieval",
        }
        try:
            content, content_type, final_url = _download(url, domains)
            title, text = _extract(content, content_type)
            if not text:
                raise SourceRejected(
                    "The source has no extractable text. No content was substituted."
                )
            result.update(title=title, url=public_source_url(final_url) or "", content=text)
        except SourceRejected as exc:
            result["warning"] = str(exc)
        except Exception:
            # Parser/network errors may embed paths, credentials or raw URLs.
            # They are deliberately not returned or logged as report content.
            result["warning"] = (
                "The official source could not be retrieved safely. Check the public URL or retry later."
            )
        results.append(result)
    return results
