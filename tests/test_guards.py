"""Unit tests for the paid-generation guards, empty-reply recovery and the
catalogue indexer. Everything external (database, Gemini, backend) is mocked."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent import orchestrator
from app.agent.tools import artwork, decor, visualize
from app.agent.tools._identity import signed_in_user_id
from app.services import indexer, visualization_service
from app.services.backend_client import absolute_media_url

SIGNED_IN = {"configurable": {"user_id": "cmuau44mz0000yxqmyjgtup90"}}
ANONYMOUS = {"configurable": {"user_id": "anon:0123456789abcdef"}}


@pytest.fixture
def no_generation(monkeypatch):
    def fail(**kwargs):
        raise AssertionError("visualize_now must not run")
    monkeypatch.setattr(visualize, "visualize_now", fail)
    monkeypatch.setattr(decor, "visualize_now", fail)


@pytest.fixture
def fake_generation(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return {"preview_url": "https://agent.example/static/generations/x.jpg"}

    monkeypatch.setattr(visualize, "visualize_now", fake)
    monkeypatch.setattr(decor, "visualize_now", fake)
    monkeypatch.setattr(visualize, "get_product_by_id", lambda _id: {"images": ["/uploads/a.webp"]})
    monkeypatch.setattr(decor, "get_product_by_id", lambda _id: {"images": ["/uploads/b.webp"]})
    return calls


# ── identity ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("config", [None, {}, {"configurable": {}}, ANONYMOUS])
def test_anonymous_or_missing_identity_is_not_signed_in(config):
    assert signed_in_user_id(config) is None


def test_signed_in_identity_comes_from_config():
    assert signed_in_user_id(SIGNED_IN) == "cmuau44mz0000yxqmyjgtup90"


@pytest.mark.parametrize("tool", [visualize.visualize_on_wall, decor.stage_decor])
def test_model_cannot_choose_the_identity(tool):
    # Anything in .args is filled in by the model; identity must not be.
    assert "user_id" not in tool.args
    assert "config" not in tool.args


# ── paid generation guard ───────────────────────────────────────────────────

def test_anonymous_wall_preview_is_refused(no_generation):
    result = visualize.visualize_on_wall.invoke(
        {"room_image_url": "https://x/room.jpg", "artwork_id": "a1"}, config=ANONYMOUS)
    assert result["error"] == "sign_in_required"


def test_anonymous_decor_preview_is_refused(no_generation):
    result = decor.stage_decor.invoke(
        {"surface_image_url": "https://x/shelf.jpg", "decor_item_id": "d1"}, config=ANONYMOUS)
    assert result["error"] == "sign_in_required"


def test_signed_in_preview_uses_the_config_identity(fake_generation):
    visualize.visualize_on_wall.invoke(
        {"room_image_url": "https://x/room.jpg", "artwork_id": "a1"}, config=SIGNED_IN)
    assert fake_generation[0]["user_id"] == "cmuau44mz0000yxqmyjgtup90"


def _run_in_graph(tool, args, config):
    """Same mechanism as production: graph config -> ToolNode -> tool.
    ToolNode needs a graph around it; invoked alone it has no runtime."""
    def emit(_state):
        return {"messages": [AIMessage(content="", tool_calls=[
            {"name": tool.name, "args": args, "id": "call-1", "type": "tool_call"}])]}

    builder = StateGraph(MessagesState)
    builder.add_node("emit", emit)
    builder.add_node("tools", ToolNode([tool]))
    builder.add_edge(START, "emit")
    builder.add_edge("emit", "tools")
    builder.add_edge("tools", END)
    return builder.compile().invoke({"messages": []}, config=config)["messages"][-1]


def test_identity_reaches_tools_through_the_graph(fake_generation):
    _run_in_graph(visualize.visualize_on_wall,
                  {"room_image_url": "https://x/r.jpg", "artwork_id": "a1"}, SIGNED_IN)
    assert fake_generation[0]["user_id"] == "cmuau44mz0000yxqmyjgtup90"


def test_anonymous_refused_through_the_graph(no_generation):
    out = _run_in_graph(visualize.visualize_on_wall,
                        {"room_image_url": "https://x/r.jpg", "artwork_id": "a1"}, ANONYMOUS)
    assert "sign_in_required" in out.content


def test_injected_identity_cannot_be_overridden_by_the_model(fake_generation):
    # A prompt-injected "user_id" argument must be ignored, not honoured.
    _run_in_graph(visualize.visualize_on_wall,
                  {"room_image_url": "https://x/r.jpg", "artwork_id": "a1",
                   "user_id": "someone-else"}, SIGNED_IN)
    assert fake_generation[0]["user_id"] == "cmuau44mz0000yxqmyjgtup90"


# ── daily limits ────────────────────────────────────────────────────────────

def _counts(monkeypatch, per_user, total):
    monkeypatch.setattr(visualization_service, "count_today",
                        lambda user_id=None: per_user if user_id else total)


def test_per_user_cap(monkeypatch):
    _counts(monkeypatch, per_user=visualization_service.MAX_GENERATIONS_PER_DAY, total=0)
    with pytest.raises(visualization_service.RateLimitExceeded):
        visualization_service.check_daily_limits("u1")


def test_global_cap_stops_everyone(monkeypatch):
    _counts(monkeypatch, per_user=0, total=visualization_service.MAX_GLOBAL_GENERATIONS_PER_DAY)
    with pytest.raises(visualization_service.RateLimitExceeded):
        visualization_service.check_daily_limits("brand-new-account")


def test_under_both_caps_is_allowed(monkeypatch):
    _counts(monkeypatch, per_user=0, total=0)
    visualization_service.check_daily_limits("u1")


def test_queue_endpoint_refuses_anonymous(monkeypatch):
    from app.main import app
    monkeypatch.setattr("app.routers.visualize.check_daily_limits", lambda _uid: None)
    res = TestClient(app).post(
        "/visualize/wall",
        headers={"X-Service-Token": "test-service-token"},
        json={"room_image_url": "r", "artwork_image_url": "a", "artwork_id": "1",
              "user_id": "anon:abc"})
    assert res.status_code == 403


# ── search tools ────────────────────────────────────────────────────────────

def test_empty_search_explains_itself(monkeypatch):
    monkeypatch.setattr(artwork, "search_artworks", lambda *a, **k: [])
    result = artwork.search_art.invoke({"query": "abstract"})
    assert result["results"] == [] and "note" in result


def test_search_failure_is_reported_not_raised(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("operator does not exist: text <=> vector")
    monkeypatch.setattr(artwork, "search_artworks", boom)
    assert "error" in artwork.search_art.invoke({"query": "abstract"})


# ── empty-reply recovery ────────────────────────────────────────────────────

class _FakeModel:
    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error

    def bind_tools(self, *a, **k):
        return self

    def invoke(self, _messages):
        if self.error:
            raise self.error
        return AIMessage(content=self.reply)


@pytest.fixture
def chat(monkeypatch):
    seen = {}
    monkeypatch.setattr(orchestrator, "_load_history", lambda _uid: [])
    monkeypatch.setattr(orchestrator, "_save_history",
                        lambda _uid, history: seen.setdefault("history", history))

    def fake_invoke(state, config=None):
        seen["config"] = config
        return {"messages": state["messages"] + [AIMessage(content="")]}

    monkeypatch.setattr(orchestrator.graph, "invoke", fake_invoke)
    return seen


def test_empty_reply_is_recovered(chat, monkeypatch):
    monkeypatch.setattr(orchestrator, "model", _FakeModel(reply="Nothing matched yet — what style do you like?"))
    reply = orchestrator.process_message("anon:x", "show me abstract art")
    assert reply == "Nothing matched yet — what style do you like?"
    assert chat["history"][-1].content == reply  # no blank turn saved


def test_empty_reply_falls_back_when_recovery_fails(chat, monkeypatch):
    monkeypatch.setattr(orchestrator, "model", _FakeModel(error=RuntimeError("quota")))
    assert orchestrator.process_message("anon:x", "hi") == orchestrator._FALLBACK_REPLY


def test_user_id_is_passed_to_the_graph_config(chat, monkeypatch):
    monkeypatch.setattr(orchestrator, "model", _FakeModel(reply="ok"))
    orchestrator.process_message("cmuau44mz0000yxqmyjgtup90", "hi")
    assert chat["config"]["configurable"]["user_id"] == "cmuau44mz0000yxqmyjgtup90"


# ── indexer ─────────────────────────────────────────────────────────────────

def test_indexer_embeds_new_and_edited_products_only():
    embedded_at = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
    known = {"same": embedded_at, "edited": embedded_at}
    assert indexer._needs_embedding({"id": "new", "updatedAt": "2026-09-30T07:00:00.000Z"}, known)
    assert indexer._needs_embedding({"id": "edited", "updatedAt": "2026-09-30T09:00:00.000Z"}, known)
    assert not indexer._needs_embedding({"id": "same", "updatedAt": "2026-09-30T08:00:00.000Z"}, known)


def test_upload_paths_resolve_to_the_backend(monkeypatch):
    monkeypatch.setattr("app.services.backend_client.BACKEND_URL", "http://backend:3000/v1")
    assert absolute_media_url("/uploads/uploads/a.webp") == "http://backend:3000/uploads/uploads/a.webp"
    assert absolute_media_url("https://cdn.example/a.webp") == "https://cdn.example/a.webp"
