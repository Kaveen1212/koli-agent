"""Per-turn JSON log line and the token-protected /metrics endpoint (P1-T17)."""
import json
import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app import config, observability
from app.agent import orchestrator
from app.main import app
from app.observability import (
    REGISTRY, TurnTracker, record_index_run, record_quota_rejection, request_id_from, user_key,
)

RAW_USER = "cmuau44mz0000yxqmyjgtup90"
SECRET_TEXT = "my-secret-message-text"


class _Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def turn_lines():
    handler = _Lines()
    log = logging.getLogger("koli.turn")
    old_level = log.level
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    yield handler.lines
    log.removeHandler(handler)
    log.setLevel(old_level)


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


@pytest.fixture
def fake_turn(monkeypatch):
    """A graph that "calls" a tool and a model through the real callback path."""
    monkeypatch.setattr(orchestrator, "_load_history", lambda _uid: [])
    monkeypatch.setattr(orchestrator, "_save_history", lambda *_a: None)

    def fake_invoke(state, config=None):
        tracker = config["callbacks"][0]
        tool_run, other_run = uuid.uuid4(), uuid.uuid4()
        tracker.on_tool_start({"name": "search_artworks"}, "ignored args", run_id=tool_run)
        tracker.on_tool_end([{"id": "a1"}], run_id=tool_run)
        tracker.on_tool_start({"name": "visualize_on_wall"}, "x", run_id=other_run)
        tracker.on_tool_end({"error": "daily_limit"}, run_id=other_run)
        msg = AIMessage(content="Here you go.",
                        usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})
        tracker.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]))
        return {"messages": state["messages"] + [msg]}

    monkeypatch.setattr(orchestrator.graph, "invoke", fake_invoke)


def test_one_json_line_per_turn_with_the_expected_shape(turn_lines, fake_turn):
    reply = orchestrator.process_message(RAW_USER, SECRET_TEXT, request_id="req-123")
    assert reply == "Here you go."
    assert len(turn_lines) == 1
    entry = json.loads(turn_lines[0])
    assert entry["event"] == "agent_turn"
    assert entry["requestId"] == "req-123"
    assert entry["status"] == "ok"
    assert entry["user"] == user_key(RAW_USER) and entry["user"].startswith("user:")
    assert isinstance(entry["latencyMs"], int) and entry["latencyMs"] >= 0
    assert entry["tokens"] == {"input": 120, "output": 30, "total": 150}
    assert [(t["name"], t["status"]) for t in entry["toolCalls"]] == [
        ("search_artworks", "ok"), ("visualize_on_wall", "error")]
    assert all(isinstance(t["ms"], int) for t in entry["toolCalls"])
    assert entry["recoveredEmptyReply"] is False
    assert "ts" in entry


def test_log_line_never_contains_the_raw_user_id_or_message(turn_lines, fake_turn):
    orchestrator.process_message(RAW_USER, SECRET_TEXT)
    line = turn_lines[0]
    assert RAW_USER not in line
    assert SECRET_TEXT not in line
    assert "ignored args" not in line  # tool arguments are not logged either


def test_anonymous_users_get_an_anon_key_and_a_request_id_is_minted(turn_lines, fake_turn):
    orchestrator.process_message("anon:abc123", "hi")
    entry = json.loads(turn_lines[0])
    assert entry["user"].startswith("anon:") and "abc123" not in entry["user"]
    assert len(entry["requestId"]) == 32  # generated, none supplied


def test_user_key_is_stable_and_distinguishes_users():
    assert user_key("u1") == user_key("u1")
    assert user_key("u1") != user_key("u2")


def test_failed_turn_is_logged_with_error_type_not_message(turn_lines, monkeypatch):
    monkeypatch.setattr(orchestrator, "_load_history", lambda _uid: [])

    def boom(state, config=None):
        raise RuntimeError(f"gemini said: {SECRET_TEXT}")

    monkeypatch.setattr(orchestrator.graph, "invoke", boom)
    before = _sample("koli_agent_turns_total", {"status": "error"})
    with pytest.raises(RuntimeError):
        orchestrator.process_message(RAW_USER, "hi")
    entry = json.loads(turn_lines[0])
    assert entry["status"] == "error"
    assert entry["errorType"] == "RuntimeError"
    assert SECRET_TEXT not in turn_lines[0]
    assert _sample("koli_agent_turns_total", {"status": "error"}) == before + 1


@pytest.mark.parametrize("supplied,expected", [
    ("abc-123_X.y:9", "abc-123_X.y:9"),
    ("has spaces", None),
    ("x" * 65, None),
    ("<script>", None),
    (None, None),
])
def test_request_id_header_is_used_only_when_well_formed(supplied, expected):
    got = request_id_from(supplied)
    assert got == expected if expected else len(got) == 32


def test_agent_route_reads_and_echoes_x_request_id(turn_lines, fake_turn, monkeypatch):
    monkeypatch.setattr("app.routers.agent.INTERNAL_SERVICE_TOKEN", "svc")
    monkeypatch.setattr("app.security.INTERNAL_SERVICE_TOKEN", "svc")
    client = TestClient(app)
    r = client.post("/agent/message", json={"user_id": RAW_USER, "message": "hi"},
                    headers={"X-Service-Token": "svc", "X-Request-ID": "backend-req-7"})
    assert r.status_code == 200
    assert r.headers["X-Request-ID"] == "backend-req-7"
    assert json.loads(turn_lines[0])["requestId"] == "backend-req-7"


def test_quota_rejections_and_index_lag_are_recorded():
    before = _sample("koli_agent_quota_rejections_total", {"scope": "global"})
    record_quota_rejection("global")
    assert _sample("koli_agent_quota_rejections_total", {"scope": "global"}) == before + 1

    record_index_run({"checked": 10, "embedded": 3, "failed": 1, "needs": 7}, failed=False)
    assert _sample("koli_agent_index_pending_products") == 4
    assert _sample("koli_agent_index_lag_seconds") < 5
    before_err = _sample("koli_agent_index_runs_total", {"result": "error"})
    record_index_run(None, failed=True)
    assert _sample("koli_agent_index_runs_total", {"result": "error"}) == before_err + 1


def test_p95_over_recent_turns(monkeypatch):
    monkeypatch.setattr(observability, "_WINDOW", observability.deque(maxlen=500))
    for i in range(1, 101):
        observability.record_latency(i / 100)
    assert observability.p95_seconds() == pytest.approx(0.96, abs=0.011)


def test_tracker_counts_model_errors_as_gemini_errors():
    t = TurnTracker("r", "u")
    before = _sample("koli_agent_gemini_errors_total", {"api": "chat", "code": "429"})
    err = RuntimeError("quota")
    err.code = 429
    t.on_llm_error(err)
    assert _sample("koli_agent_gemini_errors_total", {"api": "chat", "code": "429"}) == before + 1


# ── /metrics auth ───────────────────────────────────────────────────────────

def test_metrics_is_404_when_no_token_is_configured(monkeypatch):
    monkeypatch.setattr(config, "METRICS_TOKEN", "")
    client = TestClient(app)
    assert client.get("/metrics").status_code == 404
    # A bearer value must not open a route that is switched off.
    assert client.get("/metrics", headers={"Authorization": "Bearer "}).status_code == 404
    assert client.get("/metrics", headers={"Authorization": "Bearer anything"}).status_code == 404


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong"},
    {"Authorization": "Bearer "},
    {"Authorization": "metrics-token"},
    {"Authorization": "Basic metrics-token"},
    {"X-Service-Token": "metrics-token"},
])
def test_metrics_rejects_missing_or_wrong_token(monkeypatch, headers):
    monkeypatch.setattr(config, "METRICS_TOKEN", "metrics-token")
    r = TestClient(app).get("/metrics", headers=headers)
    assert r.status_code == 401
    assert "koli_agent" not in r.text


def test_metrics_serves_the_required_series_with_the_right_token(monkeypatch):
    monkeypatch.setattr(config, "METRICS_TOKEN", "metrics-token")
    r = TestClient(app).get("/metrics", headers={"Authorization": "Bearer metrics-token"})
    assert r.status_code == 200
    for series in ("koli_agent_turns_total", "koli_agent_turn_latency_p95_seconds",
                   "koli_agent_gemini_errors_total", "koli_agent_index_lag_seconds",
                   "koli_agent_quota_rejections_total", "koli_agent_turn_latency_seconds_bucket"):
        assert series in r.text, series
    assert RAW_USER not in r.text


def test_metrics_is_not_in_the_public_schema():
    assert "/metrics" not in app.openapi().get("paths", {})


# ── the real LangGraph callback path ────────────────────────────────────────

def test_tracker_works_through_a_real_langgraph_run():
    """The unit tests above call the callbacks by hand; this runs them via LangGraph."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.tools import tool
    from langgraph.graph import MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode, tools_condition

    @tool
    def lookup(q: str) -> dict:
        """Look something up."""
        return {"error": "nothing"} if q == "bad" else {"found": q}

    class Fake(GenericFakeChatModel):
        def bind_tools(self, *_a, **_k):
            return self

    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    model = Fake(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"q": "ok"}, "id": "c1"},
                                          {"name": "lookup", "args": {"q": "bad"}, "id": "c2"}],
                  usage_metadata=usage),
        AIMessage(content="done", usage_metadata=usage),
    ]))
    builder = StateGraph(MessagesState)
    builder.add_node("agent", lambda s: {"messages": [model.invoke(s["messages"])]})
    builder.add_node("tools", ToolNode([lookup]))
    builder.set_entry_point("agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")
    graph = builder.compile()

    tracker = TurnTracker("r1", RAW_USER)
    graph.invoke({"messages": [("user", "hi")]}, config={"callbacks": [tracker]})
    assert sorted((t["name"], t["status"]) for t in tracker.tool_calls) == [
        ("lookup", "error"), ("lookup", "ok")]
    assert (tracker.input_tokens, tracker.output_tokens) == (20, 10)
