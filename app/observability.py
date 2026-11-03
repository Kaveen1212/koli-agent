"""Per-turn structured logging and Prometheus metrics for the chat agent (P1-T17).

* One JSON log line per chat turn on the `koli.turn` logger: request id, a hashed
  user key, latency, tool calls, token usage and status. Raw user ids, message
  text, tool arguments and error messages are never logged.
* `/metrics` (see routers/metrics.py) exposes the counters below. In-process
  state: the agent runs as one uvicorn process, so there is nothing to merge.
"""
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from langchain_core.callbacks import BaseCallbackHandler
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily

from app.config import LOG_HASH_SALT

turn_logger = logging.getLogger("koli.turn")

REGISTRY = CollectorRegistry()

TURNS = Counter("koli_agent_turns_total", "Chat turns handled, by outcome.",
                ["status"], registry=REGISTRY)
TURN_LATENCY = Histogram(
    "koli_agent_turn_latency_seconds", "End-to-end latency of one chat turn.",
    buckets=(0.5, 1, 2, 3, 5, 8, 13, 21, 34, 45, 60, 90), registry=REGISTRY)
TOOL_CALLS = Counter("koli_agent_tool_calls_total", "Agent tool invocations.",
                     ["tool", "status"], registry=REGISTRY)
TOKENS = Counter("koli_agent_tokens_total", "Model tokens spent on chat turns.",
                 ["direction"], registry=REGISTRY)
GEMINI_ERRORS = Counter(
    "koli_agent_gemini_errors_total", "Failed Gemini calls (each failed attempt).",
    ["api", "code"], registry=REGISTRY)
QUOTA_REJECTIONS = Counter(
    "koli_agent_quota_rejections_total", "Requests refused by a daily generation cap.",
    ["scope"], registry=REGISTRY)

INDEX_RUNS = Counter("koli_agent_index_runs_total", "Catalogue indexer passes.",
                     ["result"], registry=REGISTRY)
INDEX_LAST_SUCCESS = Gauge(
    "koli_agent_index_last_success_timestamp_seconds",
    "Unix time the indexer last finished a pass (0 = never since start).", registry=REGISTRY)
INDEX_PENDING = Gauge(
    "koli_agent_index_pending_products",
    "Products seen needing an embedding after the last pass (lower bound when the "
    "per-run cap stopped the scan).", registry=REGISTRY)
_PROCESS_START = time.time()

# p95 over the most recent turns: the histogram gives Prometheus the full
# distribution, this gives a ready-made number without a query.
_WINDOW = deque(maxlen=500)
_window_lock = threading.Lock()


def record_latency(seconds: float) -> None:
    TURN_LATENCY.observe(seconds)
    with _window_lock:
        _WINDOW.append(seconds)


def p95_seconds() -> float:
    with _window_lock:
        data = sorted(_WINDOW)
    if not data:
        return 0.0
    return data[min(len(data) - 1, int(0.95 * len(data)))]


class _DerivedCollector:
    """Gauges computed at scrape time."""

    def collect(self):
        g = GaugeMetricFamily("koli_agent_turn_latency_p95_seconds",
                              "p95 turn latency over the last 500 turns.")
        g.add_metric([], p95_seconds())
        yield g
        last = INDEX_LAST_SUCCESS._value.get()
        lag = GaugeMetricFamily(
            "koli_agent_index_lag_seconds",
            "Seconds since the indexer last finished a pass (since process start "
            "if it has not yet).")
        lag.add_metric([], time.time() - (last or _PROCESS_START))
        yield lag


REGISTRY.register(_DerivedCollector())


def record_gemini_error(api: str, code=None) -> None:
    GEMINI_ERRORS.labels(api=api, code=str(code) if isinstance(code, int) else "other").inc()


def record_quota_rejection(scope: str) -> None:
    QUOTA_REJECTIONS.labels(scope=scope).inc()


def record_index_run(stats: Optional[dict], failed: bool) -> None:
    if failed:
        INDEX_RUNS.labels(result="error").inc()
        return
    INDEX_RUNS.labels(result="skipped" if stats.get("skipped") else "ok").inc()
    if not stats.get("skipped"):
        INDEX_LAST_SUCCESS.set(time.time())
        INDEX_PENDING.set(max(0, stats.get("needs", 0) - stats.get("embedded", 0)))


# --- identity and request id -------------------------------------------------

_REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def request_id_from(header_value: Optional[str]) -> str:
    """The caller's X-Request-ID if it is a sane token, else a fresh one."""
    if header_value and _REQUEST_ID_OK.match(header_value):
        return header_value
    return uuid.uuid4().hex


def user_key(user_id: str) -> str:
    """Stable, non-reversible key for logs: "anon:<hash>" or "user:<hash>"."""
    kind = "anon" if (user_id or "").startswith("anon:") else "user"
    digest = hashlib.sha256(f"{LOG_HASH_SALT}|{user_id}".encode()).hexdigest()[:16]
    return f"{kind}:{digest}"


# --- one turn ----------------------------------------------------------------

class TurnTracker(BaseCallbackHandler):
    """Collects tool calls and token usage while the graph runs, then logs once.

    Passed as a LangChain callback, so it also sees the tools' own threads.
    """
    raise_error = False  # observability must never break a turn

    def __init__(self, request_id: str, user_id: str, has_image: bool = False):
        self.request_id = request_id
        self.user = user_key(user_id)
        self.has_image = has_image
        self.started = time.monotonic()
        self.tool_calls: list[dict] = []
        self.input_tokens = 0
        self.output_tokens = 0
        self.recovered_empty_reply = False
        self._lock = threading.Lock()
        self._open_tools: dict = {}

    # LangChain callbacks
    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        with self._lock:
            self._open_tools[run_id] = (
                (serialized or {}).get("name") or kwargs.get("name") or "unknown",
                time.monotonic())

    def _end_tool(self, run_id, status):
        with self._lock:
            name, t0 = self._open_tools.pop(run_id, ("unknown", time.monotonic()))
            self.tool_calls.append(
                {"name": name, "ms": round((time.monotonic() - t0) * 1000), "status": status})
        TOOL_CALLS.labels(tool=name, status=status).inc()

    def on_tool_end(self, output, *, run_id, **kwargs):
        # Tools report failures as {"error": ...} dicts rather than raising.
        failed = isinstance(output, dict) and "error" in output
        content = getattr(output, "content", output)
        if isinstance(content, str) and content.lstrip().startswith("{"):
            try:
                parsed = json.loads(content)
                failed = failed or (isinstance(parsed, dict) and "error" in parsed)
            except ValueError:
                pass
        self._end_tool(run_id, "error" if failed else "ok")

    def on_tool_error(self, error, *, run_id, **kwargs):
        self._end_tool(run_id, "error")

    def on_llm_end(self, response, **kwargs):
        for gens in response.generations:
            for gen in gens:
                usage = getattr(getattr(gen, "message", None), "usage_metadata", None) or {}
                with self._lock:
                    self.input_tokens += int(usage.get("input_tokens") or 0)
                    self.output_tokens += int(usage.get("output_tokens") or 0)

    def on_llm_error(self, error, **kwargs):
        record_gemini_error("chat", getattr(error, "code", None))

    # completion
    def finish(self, status: str, error: Optional[BaseException] = None) -> dict:
        elapsed = time.monotonic() - self.started
        record_latency(elapsed)
        TURNS.labels(status=status).inc()
        if self.input_tokens:
            TOKENS.labels(direction="input").inc(self.input_tokens)
        if self.output_tokens:
            TOKENS.labels(direction="output").inc(self.output_tokens)
        entry = {
            "event": "agent_turn",
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "requestId": self.request_id,
            "user": self.user,
            "status": status,
            "latencyMs": round(elapsed * 1000),
            "toolCalls": self.tool_calls,
            "tokens": {"input": self.input_tokens, "output": self.output_tokens,
                       "total": self.input_tokens + self.output_tokens},
            "hasImage": self.has_image,
            "recoveredEmptyReply": self.recovered_empty_reply,
        }
        if error is not None:
            entry["errorType"] = type(error).__name__  # never the message: it may echo input
        turn_logger.info(json.dumps(entry, separators=(",", ":")))
        return entry
