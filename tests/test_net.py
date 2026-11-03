"""services/net.py — the only way the agent downloads an image.

Hermetic: DNS and HTTP are replaced by fakes; nothing leaves the process.
"""
import socket

import httpx
import pytest

from app.services import net
from app.services.net import UnsafeImageURL, fetch_image


@pytest.fixture
def downloads(monkeypatch):
    """Capture what _download would be asked to fetch, instead of fetching."""
    calls = []

    def fake(url, headers=None, extensions=None):
        calls.append({"url": url, "headers": headers or {}, "extensions": extensions or {}})
        return net.FetchedImage(b"img", "image/jpeg")

    monkeypatch.setattr(net, "_download", fake)
    return calls


@pytest.fixture
def dns(monkeypatch):
    """Fake resolver: set dns.table[host] = [addresses]."""
    class Resolver:
        def __init__(self):
            self.table = {}

        def __call__(self, host, port, *a, **k):
            if host not in self.table:
                raise socket.gaierror("unknown host")
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM,
                     6, "", (ip, port)) for ip in self.table[host]]

    resolver = Resolver()
    monkeypatch.setattr(net.socket, "getaddrinfo", resolver)
    return resolver


@pytest.fixture
def static_dir(tmp_path, monkeypatch):
    (tmp_path / "static" / "uploads").mkdir(parents=True)
    (tmp_path / "static" / "uploads" / "room.jpg").write_bytes(b"\xff\xd8room")
    (tmp_path / "secret.env").write_bytes(b"NOT_FOR_YOU=1")
    (tmp_path / "static" / "uploads" / "link.jpg").symlink_to(tmp_path / "secret.env")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(net, "PUBLIC_BASE_URL", "https://agent.example.com")
    return tmp_path


# ── marketplace media ────────────────────────────────────────────────────────

def test_relative_upload_path_is_fetched_from_the_internal_backend(monkeypatch, downloads):
    monkeypatch.setattr(net, "BACKEND_URL", "http://backend:3000/v1")
    fetch_image("/uploads/products/abc.webp")
    assert downloads[0]["url"] == "http://backend:3000/uploads/products/abc.webp"


@pytest.mark.parametrize("path", [
    "/v1/health/ready", "/uploads/../v1/admin", "/uploads/a/%2e%2e/b",
    "//evil.example/uploads/x.jpg", "/uploads/", "/uploads/a?x=1",
])
def test_only_clean_upload_paths_are_accepted_without_a_host(path, downloads):
    with pytest.raises(UnsafeImageURL):
        fetch_image(path)
    assert downloads == []


def test_backend_origin_is_trusted_for_uploads_only(monkeypatch, downloads):
    monkeypatch.setattr(net, "BACKEND_URL", "http://backend:3000/v1")
    fetch_image("http://backend:3000/uploads/products/abc.webp")
    for url in ("http://backend:3000/v1/internal/products/catalogue",
                "http://backend:3000/uploads/../v1/health/ready",
                "http://backend:3000/uploads/a.webp?redirect=x"):
        with pytest.raises(UnsafeImageURL):
            fetch_image(url)
    assert [c["url"] for c in downloads] == ["http://backend:3000/uploads/products/abc.webp"]


# ── the agent's own static files ─────────────────────────────────────────────

def test_own_static_file_is_read_from_disk(static_dir, downloads):
    image = fetch_image("https://agent.example.com/static/uploads/room.jpg?v=1")
    assert image.content == b"\xff\xd8room"
    assert image.content_type == "image/jpeg"
    assert downloads == []


@pytest.mark.parametrize("suffix", [
    "../secret.env", "uploads/../../secret.env", "%2e%2e/secret.env",
    "uploads/link.jpg", "uploads", "/etc/hostname",
])
def test_static_reads_cannot_leave_static(static_dir, suffix, downloads):
    with pytest.raises(UnsafeImageURL):
        fetch_image(f"https://agent.example.com/static/{suffix}")
    assert downloads == []


def test_localhost_in_the_url_does_not_mean_a_local_file(static_dir, dns, downloads):
    with pytest.raises(UnsafeImageURL):
        fetch_image("http://localhost/static/uploads/room.jpg")      # resolves nowhere
    dns.table["cdn.example.com"] = ["93.184.216.34"]
    fetch_image("https://cdn.example.com/static/x.jpg?ref=localhost")
    assert downloads[0]["headers"]["Host"] == "cdn.example.com"     # fetched, not read


# ── everything else: public internet only ────────────────────────────────────

@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "gopher://example.com/", "ftp://example.com/x.jpg",
    "data:image/png;base64,AAAA", "http://user:pw@example.com/x.jpg", "http:///x.jpg",
    "http://example.com:notaport/x.jpg",
])
def test_non_http_or_malformed_urls_are_refused(url, dns, downloads):
    with pytest.raises(UnsafeImageURL):
        fetch_image(url)
    assert downloads == []


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.5", "172.17.0.2", "192.168.1.1", "169.254.169.254",
    "100.100.100.200", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fd00:ec2::254",
    "::ffff:127.0.0.1", "64:ff9b::a00:5", "2002:a00:5::1",
])
def test_internal_addresses_are_refused_before_any_request(address, dns, downloads):
    dns.table["internal.example.com"] = [address]
    with pytest.raises(UnsafeImageURL):
        fetch_image("http://internal.example.com/room.jpg")
    assert downloads == []


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/", "http://[::1]:6379/", "http://10.0.0.5:5432/",
    "http://2130706433/", "http://0x7f.1/",
])
def test_ip_literals_are_checked_too(url, downloads):
    # Real resolver: these are IP literals (or numeric forms of one); no DNS needed.
    with pytest.raises(UnsafeImageURL):
        fetch_image(url)
    assert downloads == []


def test_one_internal_address_among_public_ones_is_enough_to_refuse(dns, downloads):
    dns.table["mixed.example.com"] = ["93.184.216.34", "10.0.0.5"]
    with pytest.raises(UnsafeImageURL):
        fetch_image("https://mixed.example.com/room.jpg")
    assert downloads == []


def test_public_fetch_connects_to_the_checked_address(dns, downloads):
    dns.table["cdn.example.com"] = ["93.184.216.34"]
    fetch_image("https://cdn.example.com/rooms/r.jpg?w=800")
    call = downloads[0]
    assert call["url"] == "https://93.184.216.34:443/rooms/r.jpg?w=800"
    assert call["headers"] == {"Host": "cdn.example.com"}
    assert call["extensions"] == {"sni_hostname": "cdn.example.com"}


# ── the download itself ──────────────────────────────────────────────────────

@pytest.fixture
def transport(monkeypatch):
    """Route _download's client through a MockTransport."""
    holder = {}
    real_client = httpx.Client

    def client(*a, **k):
        k["transport"] = httpx.MockTransport(holder["handler"])
        return real_client(*a, **k)

    monkeypatch.setattr(net.httpx, "Client", client)
    return holder


def test_redirects_are_not_followed(transport):
    transport["handler"] = lambda req: httpx.Response(
        302, headers={"location": "http://169.254.169.254/"})
    with pytest.raises(UnsafeImageURL):
        net._download("https://93.184.216.34/r.jpg")


def test_error_status_raises(transport):
    transport["handler"] = lambda req: httpx.Response(404, text="nope")
    with pytest.raises(httpx.HTTPStatusError):
        net._download("https://93.184.216.34/r.jpg")


def test_oversized_downloads_are_cut_off(transport, monkeypatch):
    monkeypatch.setattr(net, "IMAGE_FETCH_MAX_BYTES", 10)
    transport["handler"] = lambda req: httpx.Response(200, content=b"x" * 11)
    with pytest.raises(UnsafeImageURL):
        net._download("https://93.184.216.34/r.jpg")


def test_body_and_content_type_are_returned(transport):
    transport["handler"] = lambda req: httpx.Response(
        200, content=b"\xff\xd8", headers={"content-type": "image/jpeg; charset=binary"})
    image = net._download("https://93.184.216.34/r.jpg", headers={"Host": "cdn.example.com"})
    assert image == net.FetchedImage(b"\xff\xd8", "image/jpeg")


# ── MEDIA_PUBLIC_BASE_URL (storage on R2) ────────────────────────────────────

MEDIA = "https://media.artnuss.com"


@pytest.fixture
def r2(monkeypatch, dns):
    """Media host configured, resolving to a public address."""
    monkeypatch.setattr(net, "BACKEND_URL", "http://backend:3000/v1")
    monkeypatch.setattr(net, "MEDIA_PUBLIC_BASE_URL", MEDIA)
    monkeypatch.setattr("app.services.backend_client.BACKEND_URL", "http://backend:3000/v1")
    dns.table["media.artnuss.com"] = ["104.18.2.3"]
    return dns


def test_upload_path_maps_to_the_media_host_with_the_backends_key(r2, downloads):
    # /uploads/<key> -> <base>/<key>; the key itself is "uploads/<hex>.webp".
    fetch_image("/uploads/uploads/abc.webp")
    assert downloads[0]["url"] == "https://104.18.2.3:443/uploads/abc.webp"
    assert downloads[0]["headers"] == {"Host": "media.artnuss.com"}
    assert downloads[0]["extensions"] == {"sni_hostname": "media.artnuss.com"}


def test_backend_origin_upload_url_also_maps_to_the_media_host(r2, downloads):
    fetch_image("http://backend:3000/uploads/uploads/abc.webp")
    assert downloads[0]["url"] == "https://104.18.2.3:443/uploads/abc.webp"


def test_absolute_media_host_url_is_fetched_as_a_public_url(r2, downloads):
    fetch_image(f"{MEDIA}/uploads/abc.webp")
    assert downloads[0]["url"] == "https://104.18.2.3:443/uploads/abc.webp"


def test_media_host_keeps_the_public_dns_check(r2, downloads):
    r2.table["media.artnuss.com"] = ["104.18.2.3", "10.0.0.5"]
    with pytest.raises(UnsafeImageURL):
        fetch_image("/uploads/uploads/abc.webp")
    assert downloads == []


@pytest.mark.parametrize("path", [
    "/uploads/../v1/admin", "/uploads/a/%2e%2e/b", "/uploads/", "/uploads/a?x=1",
    "/uploads/.incoming/abc.webp", "/uploads/uploads/.hidden/abc.webp",
])
def test_unclean_paths_are_still_refused_with_a_media_host(r2, downloads, path):
    with pytest.raises(UnsafeImageURL):
        fetch_image(path)
    assert downloads == []


def test_redirects_are_still_not_followed_on_the_media_host(r2, monkeypatch):
    class Resp:
        is_redirect = True
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Client:
        def __init__(self, **kw):
            assert kw["follow_redirects"] is False
            assert kw["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, *a, **k):
            return Resp()

    monkeypatch.setattr(net.httpx, "Client", Client)
    with pytest.raises(UnsafeImageURL, match="redirect"):
        fetch_image("/uploads/uploads/abc.webp")


def test_absolute_media_url_maps_uploads_and_passes_absolute_through(r2):
    from app.services.backend_client import absolute_media_url
    assert absolute_media_url("/uploads/uploads/a.webp") == f"{MEDIA}/uploads/a.webp"
    assert absolute_media_url(f"{MEDIA}/uploads/new.webp") == f"{MEDIA}/uploads/new.webp"
    assert absolute_media_url("https://cdn.example/a.webp") == "https://cdn.example/a.webp"


def test_absolute_media_url_without_a_media_host_is_unchanged(monkeypatch):
    from app.services.backend_client import absolute_media_url
    monkeypatch.setattr(net, "MEDIA_PUBLIC_BASE_URL", "")
    monkeypatch.setattr("app.services.backend_client.BACKEND_URL", "http://backend:3000/v1")
    assert absolute_media_url("/uploads/uploads/a.webp") == "http://backend:3000/uploads/uploads/a.webp"
    assert absolute_media_url(f"{MEDIA}/uploads/a.webp") == f"{MEDIA}/uploads/a.webp"


def test_media_host_config_accepts_https_only(caplog):
    from app.config import parse_media_public_base_url as parse
    assert parse("") == "" and parse("   ") == ""
    assert parse(" https://media.artnuss.com/ ") == MEDIA
    assert parse("https://media.artnuss.com/base/") == "https://media.artnuss.com/base"
    with caplog.at_level("WARNING"):
        for bad in ("http://media.artnuss.com", "media.artnuss.com", "ftp://x/y",
                    "https://", "https://u:p@media.artnuss.com", "https://m.example/?q=1",
                    "https://m.example:abc"):
            assert parse(bad) == "", bad
    assert caplog.text.count("MEDIA_PUBLIC_BASE_URL is not a valid https URL") == 7


def test_chat_image_check_accepts_both_media_shapes():
    """orchestrator._run_turn only treats http(s) and /uploads/ image URLs as
    attachments; both the legacy and the R2 shape must pass."""
    import inspect
    from app.agent import orchestrator
    src = inspect.getsource(orchestrator._run_turn)
    assert 'startswith(("http://", "https://", "/uploads/"))' in src
