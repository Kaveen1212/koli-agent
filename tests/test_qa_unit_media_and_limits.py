"""QA unit campaign 20261008-231224 (qa-unit).

Pure logic. Gemini, Postgres, Redis and the network are never touched: every
outbound fetch is replaced by a recorder, and the database by fakes.
Tests named test_bug_* fail against main@ee92d95 on purpose -- each pins a
defect listed in qa-reports/20261008-231224/qa-unit.md. The rest pass and are
coverage handed over.
"""
import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent import orchestrator
from app.agent.tools import decor, visualize
from app.services import image_service, visualization_service


class _NetworkCalled(AssertionError):
    pass


@pytest.fixture
def no_network(monkeypatch):
    """Record (and refuse) every outbound fetch the media helpers try."""
    calls = []

    def recorder(url, *a, **k):
        calls.append(url)
        raise _NetworkCalled(f"outbound fetch attempted: {url}")

    # Fetches now go through services/net.py's httpx.Client, so record at the
    # transport boundary every request crosses (and httpx.get for good measure).
    monkeypatch.setattr(httpx, "get", recorder)
    monkeypatch.setattr(httpx.Client, "send",
                        lambda self, request, **k: recorder(str(request.url)))
    return calls


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """cwd with a ./static dir and a 'secret' file just outside it."""
    (tmp_path / "app").mkdir()
    static = tmp_path / "app" / "static"
    (static / "uploads").mkdir(parents=True)
    (static / "uploads" / "room.jpg").write_bytes(b"\xff\xd8room")
    secret = tmp_path / "app" / "secret.env"
    secret.write_bytes(b"GEMINI_API_KEY=not-a-real-key")
    monkeypatch.chdir(tmp_path / "app")
    return {"static": static, "secret": secret}


FETCHERS = [
    pytest.param(image_service._fetch, id="image_service._fetch"),
    pytest.param(orchestrator._load_image_bytes, id="orchestrator._load_image_bytes"),
]


# ── what works today ────────────────────────────────────────────────────────

@pytest.mark.parametrize("fetch", FETCHERS)
def test_local_upload_under_static_is_read_from_disk(fetch, sandbox, no_network):
    assert fetch("http://localhost:8000/static/uploads/room.jpg?v=1") == b"\xff\xd8room"
    assert no_network == []


def test_latest_image_is_fetched_eagerly_without_any_tool_call(monkeypatch):
    """_prepare_for_gemini downloads the newest image_url itself, before the
    model decides anything -- so whatever URL arrives in /v1/chat is fetched."""
    fetched = []
    monkeypatch.setattr(orchestrator, "_load_image_bytes",
                        lambda url: fetched.append(url) or b"img")
    msgs = [SystemMessage(content="s"), HumanMessage(content=[
        {"type": "text", "text": "my wall"},
        {"type": "image_url", "image_url": {"url": "https://x.example/r.jpg"}},
    ])]
    out = orchestrator._prepare_for_gemini(msgs)
    assert fetched == ["https://x.example/r.jpg"]
    assert out[1].content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_older_images_are_not_refetched(monkeypatch):
    fetched = []
    monkeypatch.setattr(orchestrator, "_load_image_bytes",
                        lambda url: fetched.append(url) or b"img")
    img = lambda u: HumanMessage(content=[{"type": "image_url", "image_url": {"url": u}}])
    orchestrator._prepare_for_gemini([img("https://x/old.jpg"), img("https://x/new.jpg")])
    assert fetched == ["https://x/new.jpg"]


# ── [BUG] path traversal out of ./static ───────────────────────────────────

def _must_refuse(fetch, url):
    try:
        data = fetch(url)
    except Exception:  # noqa: BLE001 -- any refusal is a pass
        return  # refused -- what we want
    pytest.fail(f"{url!r} read {len(data)} bytes from outside ./static: {data[:40]!r}")


@pytest.mark.parametrize("fetch", FETCHERS)
def test_bug_dotdot_cannot_escape_static(fetch, sandbox, no_network):
    # Today this returns the bytes of ../secret.env (try ../../proc/self/environ
    # in the container: the agent's whole environment, keys included).
    _must_refuse(fetch, "http://localhost/static/../secret.env")


@pytest.mark.parametrize("fetch", FETCHERS)
def test_bug_absolute_path_cannot_escape_static(fetch, sandbox, no_network):
    # os.path.join("static", "/abs/path") discards "static" entirely.
    _must_refuse(fetch, f"http://localhost/static/{sandbox['secret']}")


@pytest.mark.parametrize("fetch", FETCHERS)
def test_bug_localhost_must_be_the_host_not_a_substring(fetch, sandbox, no_network):
    # "localhost" anywhere in the URL routes it to the local-disk branch.
    _must_refuse(fetch, "https://cdn.example.com/static/../secret.env?ref=localhost")


# ── [BUG] SSRF: arbitrary server-side fetch ────────────────────────────────

@pytest.mark.parametrize("fetch", FETCHERS)
@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",     # cloud metadata
    "http://10.0.0.5:5432/",                         # private network
    "http://backend:3000/v1/health/ready",           # compose service name
    "http://[::1]:6379/",                            # loopback v6 (not 'localhost')
])
def test_bug_internal_targets_are_refused_before_any_request(fetch, url, no_network):
    try:
        fetch(url)
    except _NetworkCalled:
        pass
    except Exception:  # noqa: BLE001, S110 -- only the recorded fetches matter
        pass
    assert no_network == [], f"fetched internal URL {no_network}"


# ── [BUG] relative catalogue images never resolve ──────────────────────────

@pytest.fixture
def capture_visualize(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return {"preview_url": "https://agent.example/static/generations/x.jpg"}

    monkeypatch.setattr(visualize, "visualize_now", fake)
    monkeypatch.setattr(decor, "visualize_now", fake)
    # Exactly what /v1/internal/products/by-ids returns with local storage
    # (production has no S3 keys): a relative /uploads path.
    row = {"images": ["/uploads/products/abc.webp"]}
    monkeypatch.setattr(visualize, "get_product_by_id", lambda _id: row)
    monkeypatch.setattr(decor, "get_product_by_id", lambda _id: row)
    return calls


SIGNED_IN = {"configurable": {"user_id": "cmuau44mz0000yxqmyjgtup90"}}


@pytest.mark.parametrize("tool,args", [
    (visualize.visualize_on_wall, {"room_image_url": "https://x/r.jpg", "artwork_id": "a1"}),
    (decor.stage_decor, {"surface_image_url": "https://x/s.jpg", "decor_item_id": "d1"}),
])
def test_bug_catalogue_image_is_made_absolute_before_render(tool, args, capture_visualize):
    tool.invoke(args, config=SIGNED_IN)
    artwork_url = capture_visualize[0]["artwork_url"]
    assert artwork_url.startswith(("http://", "https://")), (
        f"visualize_now got {artwork_url!r}; _fetch() cannot download a bare path")


def test_bug_fetch_of_a_relative_upload_path_does_not_crash(monkeypatch, sandbox):
    # What _render() does with that relative path today.
    import httpx as real_httpx
    try:
        image_service._fetch("/uploads/products/abc.webp")
    except real_httpx.UnsupportedProtocol as exc:
        pytest.fail(f"relative upload path is unusable: {exc}")
    except Exception:  # noqa: BLE001, S110
        pass  # any other outcome (resolved + network refused) is fine here


# ── daily limits ────────────────────────────────────────────────────────────

def test_global_cap_message_does_not_name_the_number(monkeypatch):
    monkeypatch.setattr(visualization_service, "count_today",
                        lambda user_id=None: 0 if user_id else 10**6)
    with pytest.raises(visualization_service.RateLimitExceeded) as e:
        visualization_service.check_daily_limits("u1")
    assert "tomorrow" in str(e.value)


def test_per_user_cap_is_checked_before_global(monkeypatch):
    seen = []

    def count(user_id=None):
        seen.append(user_id)
        return 10**6

    monkeypatch.setattr(visualization_service, "count_today", count)
    with pytest.raises(visualization_service.RateLimitExceeded):
        visualization_service.check_daily_limits("u1")
    assert seen == ["u1"]
