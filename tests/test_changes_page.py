"""The change-feed page is validated before the indexer sees it (SEC-9).

A page that breaks the contract in docs/CATALOGUE_FEED.md must raise
BackendUnavailable, so the cursor is not advanced and the page is read again;
it must never reach the indexer as a KeyError or a cursor that skips changes.
"""
import copy

import httpx
import pytest

from app import observability as obs
from app.services import backend_client as bc
from app.services import indexer
from app.services.backend_client import BackendUnavailable, changes_page

T = "2026-11-01T10:00:00.000Z"


def row(seq, pid="cmabc", op="UPSERT", reason="CONTENT", cu=T):
    return {"seq": seq, "productId": pid, "op": op, "reason": reason,
            "contentUpdatedAt": cu if op == "UPSERT" else None}


def page(items, next_since=None, has_more=False, **extra):
    nxt = next_since if next_since is not None else (items[-1]["seq"] if items else 0)
    return {"items": items, "nextSince": nxt, "hasMore": has_more,
            "lag": {"seconds": 10, "withheld": 0}, **extra}


@pytest.fixture
def feed(monkeypatch):
    """changes_page(...) against a scripted /changes body."""
    box = {}
    monkeypatch.setattr(bc, "_get", lambda path, params: copy.deepcopy(box["body"]))

    def call(body, since=0, limit=200):
        box["body"] = body
        return changes_page(since=since, limit=limit)
    return call


# -- the good page --------------------------------------------------------------

def test_the_documented_page_passes_and_keeps_its_shape(feed):
    got = feed(page([row(4121, op="DELETE", reason="SOLD"), row(4122, reason="CONTENT")]),
               since=4000)
    assert got["nextSince"] == 4122 and got["hasMore"] is False
    assert [(i["seq"], i["op"], i["reason"]) for i in got["items"]] == [
        (4121, "DELETE", "SOLD"), (4122, "UPSERT", "CONTENT")]
    assert got["items"][0]["contentUpdatedAt"] is None
    # What the indexer parses: an ISO string with an offset.
    assert indexer._parse_ts(got["items"][1]["contentUpdatedAt"]).isoformat() == \
        "2026-11-01T10:00:00+00:00"


def test_an_empty_page_equals_since(feed):
    assert feed(page([], next_since=4122), since=4122)["nextSince"] == 4122


def test_every_reason_the_backend_documents_is_accepted(feed):
    ups = [row(i + 1, reason=r) for i, r in enumerate(sorted(bc.UPSERT_REASONS))]
    dels = [row(100 + i, op="DELETE", reason=r) for i, r in enumerate(sorted(bc.DELETE_REASONS))]
    got = feed(page(ups + dels))
    assert {i["reason"] for i in got["items"]} == bc.CHANGE_REASONS
    assert len(bc.CHANGE_REASONS) == 17


def test_a_new_backend_reason_is_accepted_and_counted_as_other(feed):
    got = feed(page([row(1, op="DELETE", reason="SOMETHING_NEW")]))
    assert got["items"][0]["reason"] == bc.OTHER_REASON


def test_extra_keys_from_a_newer_backend_are_ignored(feed):
    body = page([{**row(1), "newField": 1}], extra_top=True)
    assert "newField" not in feed(body)["items"][0]


# -- malformed pages ------------------------------------------------------------

@pytest.mark.parametrize("bad_item", [
    {k: v for k, v in row(1).items() if k != "productId"},           # missing key
    {k: v for k, v in row(1).items() if k != "seq"},
    {k: v for k, v in row(1).items() if k != "op"},
    {**row(1), "seq": "1"},                                          # wrong type
    {**row(1), "seq": 1.5},
    {**row(1), "seq": True},
    {**row(1), "seq": -3},
    {**row(1), "productId": ""},
    {**row(1), "productId": 12},
    {**row(1), "productId": "x" * 65},
    {**row(1), "op": "PATCH"},
    {**row(1), "op": "upsert"},
    {**row(1), "reason": None},
    {**row(1), "reason": ""},
    {**row(1), "reason": ["SOLD"]},
    {**row(1), "contentUpdatedAt": "yesterday"},
    {**row(1), "contentUpdatedAt": "2026-11-01T10:00:00"},         # no timezone
    {**row(1), "contentUpdatedAt": 1730000000},
    row(1, op="UPSERT", reason="SOLD"),                              # reason of the other op
    row(1, op="DELETE", reason="PUBLISHED"),
    "not an object",
    None,
])
def test_a_malformed_item_is_refused(feed, bad_item):
    with pytest.raises(BackendUnavailable, match="/changes"):
        feed({"items": [bad_item], "nextSince": 1, "hasMore": False})


@pytest.mark.parametrize("body", [
    None, [], "ok", {}, {"items": []}, {"nextSince": 1},
    {"items": [], "nextSince": 0},                                   # hasMore missing
    {"items": None, "nextSince": 0, "hasMore": False},
    {"items": {}, "nextSince": 0, "hasMore": False},
    {"items": [], "nextSince": "0", "hasMore": False},
    {"items": [], "nextSince": 0.0, "hasMore": False},
    {"items": [], "nextSince": None, "hasMore": False},
    {"items": [], "nextSince": -1, "hasMore": False},
    {"items": [], "nextSince": 0, "hasMore": "false"},
    {"items": [], "nextSince": 0, "hasMore": 0},
    {"items": [], "nextSince": 0, "hasMore": False, "lag": {"seconds": -1, "withheld": 0}},
    {"items": [], "nextSince": 0, "hasMore": False, "lag": "10"},
])
def test_a_malformed_page_is_refused(feed, body):
    with pytest.raises(BackendUnavailable, match="/changes"):
        feed(body)


def test_more_than_500_items_is_refused(feed):
    items = [row(i + 1, pid=f"p{i}") for i in range(501)]
    with pytest.raises(BackendUnavailable):
        feed(page(items), limit=500)


def test_more_items_than_the_requested_limit_is_refused(feed):
    with pytest.raises(BackendUnavailable, match="limit 1"):
        feed(page([row(1), row(2)]), limit=1)


def test_an_error_message_does_not_echo_the_response_body(feed):
    with pytest.raises(BackendUnavailable) as e:
        feed(page([{**row(1), "productId": "SECRET-" + "x" * 80}]))
    assert "SECRET" not in str(e.value)


# -- the cursor rules -----------------------------------------------------------

def test_next_since_beyond_the_last_item_is_refused(feed):
    with pytest.raises(BackendUnavailable, match="beyond the last item"):
        feed(page([row(5), row(6)], next_since=9_999_999), since=4)


def test_next_since_beyond_since_on_an_empty_page_is_refused(feed):
    with pytest.raises(BackendUnavailable, match="empty page"):
        feed(page([], next_since=9_999_999), since=10)


def test_items_at_or_before_since_are_refused(feed):
    with pytest.raises(BackendUnavailable, match="not after since"):
        feed(page([row(10)]), since=10)


def test_items_out_of_order_or_repeated_are_refused(feed):
    with pytest.raises(BackendUnavailable, match="ascending"):
        feed(page([row(6), row(5)], next_since=6), since=1)
    with pytest.raises(BackendUnavailable, match="ascending"):
        feed(page([row(5), row(5)], next_since=5), since=1)


def test_next_since_below_since_is_passed_on_so_the_indexer_resyncs(feed):
    # Already handled by indexer._drain_feed ("feed went backwards"): not an
    # error here, or the cursor could never recover from a reset feed.
    assert feed(page([], next_since=3), since=500)["nextSince"] == 3


# -- a 200 that is not JSON -------------------------------------------------------

def _serve(monkeypatch, response):
    monkeypatch.setattr(bc, "BACKEND_URL", "http://backend.test/v1")
    monkeypatch.setattr(bc.httpx, "get", lambda *a, **k: response)


@pytest.mark.parametrize("content", [b"<html>502 from a proxy</html>", b"", b'{"data": '])
def test_a_non_json_200_is_backend_unavailable(monkeypatch, content):
    _serve(monkeypatch, httpx.Response(200, content=content,
                                       request=httpx.Request("GET", "http://backend.test")))
    with pytest.raises(BackendUnavailable, match="non-JSON"):
        changes_page(since=0)


def test_a_real_wrapped_response_is_unwrapped_and_validated(monkeypatch):
    body = {"success": True, "data": page([row(1)]), "timestamp": T}
    _serve(monkeypatch, httpx.Response(200, json=body,
                                       request=httpx.Request("GET", "http://backend.test")))
    assert changes_page(since=0)["nextSince"] == 1


# -- the indexer leaves the cursor alone ------------------------------------------

def test_a_malformed_page_stops_the_pass_without_moving_the_cursor(monkeypatch):
    from tests.test_indexer_feed import NOW, Backend, FakeStore

    be = Backend(monkeypatch)
    monkeypatch.setattr(indexer, "INDEX_SOLD_GRACE_DAYS", 0)
    store = FakeStore()
    be.sellable = {"a": {"id": "a", "title": "A", "images": []}}
    indexer.sync(store, 100, NOW)
    cursor = store.st["feed_cursor"]

    # The real changes_page, fed a page whose nextSince jumps far ahead.
    monkeypatch.setattr(indexer, "changes_page", bc.changes_page)
    monkeypatch.setattr(bc, "_get", lambda path, params: page([row(cursor + 1, pid="a")],
                                                              next_since=cursor + 10_000))
    store.st["resync_due_at"] = NOW.replace(year=2027)
    with pytest.raises(BackendUnavailable):
        indexer.sync(store, 100, NOW)
    assert store.st["feed_cursor"] == cursor


# -- the Prometheus label ---------------------------------------------------------

def test_the_prune_reason_label_is_bounded():
    for r in list(bc.CHANGE_REASONS) + ["NOT_SELLABLE", "RESYNC"]:
        assert obs.prune_reason_label(r) == r
    for junk in ("", "sold", "x" * 500, "SOLD; DROP", None, 7, "UNKNOWN"):
        assert obs.prune_reason_label(junk) == "OTHER"


def test_record_index_run_never_creates_a_series_for_an_unknown_reason():
    junk = [f"junk-{i}" for i in range(50)]
    before = {s.labels["reason"] for m in obs.INDEX_PRUNED.collect() for s in m.samples
              if "reason" in s.labels}
    obs.record_index_run({"needs": 0, "embedded": 0, "caught_up": True,
                          "pruned_reasons": {**{j: 1 for j in junk}, "SOLD": 1}}, failed=False)
    after = {s.labels["reason"] for m in obs.INDEX_PRUNED.collect() for s in m.samples
             if "reason" in s.labels}
    assert not set(junk) & after
    assert after - before <= {"OTHER", "SOLD"}
    assert obs.REGISTRY.get_sample_value("koli_agent_index_pruned_total",
                                         {"reason": "OTHER"}) >= 50
