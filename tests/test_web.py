import httpx
import pytest

from leadqual.web import FetchError, check_public_url, fetch_page, parse_page

PUBLIC = ["93.184.216.34"]


def public(_host: str) -> list[str]:
    return PUBLIC


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def html(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, headers={"content-type": "text/html; charset=utf-8"}, text=body)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://acme.io",
        "http://acme.io:8080/",
        "http://acme.io:notaport/",
        "https:///nohost",
    ],
)
def test_non_web_urls_are_refused_before_any_lookup(url):
    def never(_host):
        raise AssertionError("must not resolve")

    with pytest.raises(FetchError):
        check_public_url(url, never)


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "fd00::1"],
)
def test_internal_addresses_are_refused(address):
    with pytest.raises(FetchError, match="not a public address"):
        check_public_url("http://internal.example/", lambda _host: [address])


def test_host_with_one_internal_address_is_refused():
    with pytest.raises(FetchError):
        check_public_url("http://mixed.example/", lambda _host: [*PUBLIC, "10.0.0.5"])


def test_unresolvable_host_is_a_fetch_error():
    def fail(_host):
        raise OSError("no such host")

    with pytest.raises(FetchError, match="cannot resolve"):
        check_public_url("https://nowhere.test/", fail)


def test_page_text_drops_scripts_and_collapses_whitespace():
    page = parse_page(
        "https://acme.io/",
        "<html><script>var x=1</script><style>p{}</style><p>Hello\n\n  <b>world</b></p></html>",
    )

    assert page.text == "Hello world"


def test_page_links_are_absolute_same_host_and_unique():
    page = parse_page(
        "https://www.acme.io/",
        '<a href="/about">About</a><a href="about#team">Team</a>'
        '<a href="https://other.example/x">Other</a><a href="https://acme.io/jobs">Jobs</a>',
    )

    assert page.links == ("https://www.acme.io/about", "https://acme.io/jobs")


def test_fetch_follows_redirects_and_reports_the_final_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "acme.io":
            return httpx.Response(301, headers={"location": "https://www.acme.com/home"})
        return html("<p>Welcome to Acme</p>")

    page = fetch_page("https://acme.io/", client=client_for(handler), resolve=public)

    assert page.url == "https://www.acme.com/home"
    assert page.host == "acme.com"
    assert page.text == "Welcome to Acme"


def test_redirect_to_an_internal_address_is_refused():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    def resolve(host: str) -> list[str]:
        return ["169.254.169.254"] if host == "169.254.169.254" else PUBLIC

    with pytest.raises(FetchError, match="not a public address"):
        fetch_page("https://acme.io/", client=client_for(handler), resolve=resolve)


def test_redirect_loop_gives_up():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": str(request.url)})

    with pytest.raises(FetchError, match="too many redirects"):
        fetch_page("https://acme.io/", client=client_for(handler), resolve=public)


@pytest.mark.parametrize("status", [403, 404, 500])
def test_error_status_is_a_fetch_error(status):
    with pytest.raises(FetchError, match=f"HTTP {status}"):
        fetch_page(
            "https://acme.io/", client=client_for(lambda _r: html("nope", status)), resolve=public
        )


def test_non_html_content_is_refused():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    with pytest.raises(FetchError, match="not an HTML page"):
        fetch_page("https://acme.io/doc", client=client_for(handler), resolve=public)


def test_network_failure_is_a_fetch_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(FetchError, match="ConnectTimeout"):
        fetch_page("https://acme.io/", client=client_for(handler), resolve=public)


def test_one_malformed_link_does_not_cost_the_whole_page():
    """Found in review: a single bad href crashed the run before anything was recorded."""
    page = parse_page(
        "https://acme.io/",
        '<p>Welcome</p><a href="http://[bad">x</a><a href="//[::1">y</a><a href="/about">About</a>',
    )

    assert page.text == "Welcome x y About"
    assert page.links == ("https://acme.io/about",)


@pytest.mark.parametrize("location", ["http://[bad", "http://" + "a" * 64 + ".example/"])
def test_malformed_redirect_target_is_a_fetch_error_not_a_crash(location):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": location})

    def resolve(host: str) -> list[str]:
        if len(host) > 63:
            raise UnicodeError("label too long")  # what socket.getaddrinfo raises
        return PUBLIC

    with pytest.raises(FetchError):
        fetch_page("https://acme.io/", client=client_for(handler), resolve=resolve)
