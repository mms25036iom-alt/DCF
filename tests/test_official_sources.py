from io import BytesIO

import pytest
from pypdf import PdfWriter

from services.serverless import sources


def public_dns(host, port, **kwargs):
    return [(sources.socket.AF_INET, sources.socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]


class FakeResponse:
    def __init__(self, body=b"<html><title>Filing</title><p>Revenue grew.</p></html>", **headers):
        self.status = 200
        self.headers = {"Content-Type": "text/html", **headers}
        self.body = BytesIO(body)

    def getheader(self, name):
        return self.headers.get(name)

    def read(self, length):
        return self.body.read(length)


def connections(monkeypatch, responses):
    opened = []

    class Connection:
        sock = None

        def __init__(self, hostname, address, timeout):
            self.hostname, self.address, self.timeout = hostname, address, timeout
            self.response = responses[len(opened)]
            self.closed = False
            opened.append(self)

        def request(self, method, target, headers):
            self.method, self.target, self.headers = method, target, headers

        def getresponse(self):
            return self.response

        def close(self):
            self.closed = True

    monkeypatch.setattr(sources, "_PinnedHTTPSConnection", Connection)
    monkeypatch.setattr(sources.socket, "getaddrinfo", public_dns)
    return opened


@pytest.mark.parametrize(
    "url",
    [
        "http://sec.gov/filing",
        "https://sec.gov:444/filing",
        "https://user:secret@sec.gov/filing",
        "https://sec.gov.attacker.org/filing",
        "https://127.0.0.1/filing",
        "https://localhost/filing",
        "https://sec.gov\\@attacker.org/filing",
        "https://sec.gov/filing\r\nInjected: yes",
    ],
)
def test_source_url_validation_happens_before_network(monkeypatch, url):
    def unexpected_dns(*args, **kwargs):
        pytest.fail("Unapproved source must not perform DNS lookup")

    monkeypatch.setattr(sources.socket, "getaddrinfo", unexpected_dns)
    with pytest.raises(sources.SourceRejected):
        sources._validated_target(url, sources.OFFICIAL_DOMAINS)


@pytest.mark.parametrize("private", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1"])
def test_any_private_dns_answer_rejects_the_whole_destination(monkeypatch, private):
    monkeypatch.setattr(
        sources.socket,
        "getaddrinfo",
        lambda *args, **kwargs: public_dns("sec.gov", 443)
        + [(sources.socket.AF_INET6, 1, 6, "", (private, 443))],
    )
    with pytest.raises(sources.SourceRejected, match="exclusively to public"):
        sources._validated_target("https://sec.gov/filing", sources.OFFICIAL_DOMAINS)


def test_pinned_connection_uses_validated_ip_and_verified_original_tls_host(monkeypatch):
    calls = []

    class RawSocket:
        def close(self):
            calls.append("closed")

    class Context:
        def wrap_socket(self, connection, server_hostname):
            calls.append((connection, server_hostname))
            return "verified socket"

    raw = RawSocket()
    monkeypatch.setattr(sources.ssl, "create_default_context", lambda: Context())
    monkeypatch.setattr(
        sources.socket,
        "create_connection",
        lambda target, timeout: calls.append((target, timeout)) or raw,
    )
    connection = sources._PinnedHTTPSConnection("sec.gov", "8.8.8.8", 4.0)
    connection.connect()
    assert calls == [(("8.8.8.8", 443), 4.0), (raw, "sec.gov")]
    assert connection.sock == "verified socket"


def test_unapproved_redirect_is_never_connected_to(monkeypatch):
    redirect = FakeResponse(Location="https://internal.attacker.org/secret")
    redirect.status = 302
    opened = connections(monkeypatch, [redirect])
    result = sources.fetch_official_sources(["https://sec.gov/filing"], None)[0]
    assert len(opened) == 1 and opened[0].closed
    assert result["content"] == ""
    assert "official website" in result["warning"]


def test_allowed_redirect_is_dns_checked_again(monkeypatch):
    redirect = FakeResponse(Location="https://www.sec.gov/filing")
    redirect.status = 302
    opened = connections(monkeypatch, [redirect])

    def changed_dns(host, port, **kwargs):
        if host == "www.sec.gov":
            return [(sources.socket.AF_INET, 1, 6, "", ("127.0.0.1", port))]
        return public_dns(host, port)

    monkeypatch.setattr(sources.socket, "getaddrinfo", changed_dns)
    result = sources.fetch_official_sources(["https://sec.gov/filing"], None)[0]
    assert len(opened) == 1
    assert "exclusively to public" in result["warning"]


def test_company_website_host_allows_only_that_domain_and_subdomains(monkeypatch):
    monkeypatch.setattr(sources.socket, "getaddrinfo", public_dns)
    allowed = sources._allowed_domains("https://www.example.org/about")
    normalized, target, addresses = sources._validated_target(
        "https://investors.example.org/report?year=2026#page=2", allowed
    )
    assert normalized == "https://investors.example.org/report?year=2026"
    assert target == "/report?year=2026" and addresses == ["8.8.8.8"]
    with pytest.raises(sources.SourceRejected):
        sources._validated_target("https://example.org.attacker.org/report", allowed)


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(**{"Content-Length": str(sources.MAX_DOCUMENT_BYTES + 1)}),
        FakeResponse(**{"Content-Length": "not-a-number"}),
        FakeResponse(**{"Content-Encoding": "gzip"}),
        FakeResponse(body=b"x" * (sources.MAX_DOCUMENT_BYTES + 1)),
    ],
)
def test_encoded_or_oversized_documents_fail_closed(monkeypatch, response):
    opened = connections(monkeypatch, [response])
    result = sources.fetch_official_sources(["https://sec.gov/filing"], None)[0]
    assert result["warning"] and not result["content"]
    assert opened[0].closed


def test_html_content_and_public_citation_are_bounded_and_sanitized(monkeypatch):
    body = (
        "<title>Annual report</title><script>untrusted script text</script>"
        "<style>hidden style</style><p>Revenue disclosure.</p><p>"
        + "Observation. " * 4000
        + "</p>"
    ).encode()
    opened = connections(monkeypatch, [FakeResponse(body=body)])
    result = sources.fetch_official_sources(["https://sec.gov/filing?token=private#page"], None)[0]
    assert result["title"] == "Annual report" and result["warning"] is None
    assert result["url"] == "https://sec.gov/filing"
    assert "untrusted script text" not in result["content"] and "hidden style" not in result["content"]
    assert "Revenue disclosure" in result["content"]
    assert len(result["content"]) <= sources.MAX_TEXT_CHARACTERS
    assert opened[0].headers["Accept-Encoding"] == "identity"


def test_pdf_page_limit_is_checked_before_extracting_text():
    writer = PdfWriter()
    for _ in range(sources.MAX_PDF_PAGES + 1):
        writer.add_blank_page(width=100, height=100)
    data = BytesIO()
    writer.write(data)
    with pytest.raises(sources.SourceRejected, match="30-page"):
        sources._extract(data.getvalue(), "application/pdf")


def test_parser_and_network_error_details_are_not_returned(monkeypatch):
    def private_failure(*args, **kwargs):
        raise RuntimeError("credential=secret C:\\private\\filing.pdf https://user:secret@sec.gov/")

    monkeypatch.setattr(sources, "_download", private_failure)
    result = sources.fetch_official_sources(["https://sec.gov/filing?api_key=secret"], None)[0]
    assert "secret" not in str(result) and "private\\filing" not in str(result)
    assert result["warning"] and result["content"] == ""


def test_source_count_and_supported_document_type_are_bounded():
    with pytest.raises(ValueError, match="at most three"):
        sources.fetch_official_sources(["https://sec.gov/filing"] * 4, None)
    with pytest.raises(sources.SourceRejected, match="HTML, text, and PDF"):
        sources._extract(b"arbitrary data", "application/octet-stream")
