"""The change-feed indexer (P2-T07 / K-1). The backend and Gemini are mocked; the
store is an in-memory stand-in with the same interface as PgStore (the SQL is
covered by the database test at the bottom, which skips without a database)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import indexer

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
T1 = "2026-11-01T10:00:00.000Z"
T2 = "2026-11-02T10:00:00.000Z"


def product(pid="p1", **kw):
    return {"id": pid, "title": f"Title {pid}", "description": "desc", "category": "Oil",
            "medium": "Canvas", "images": [f"/uploads/{pid}.webp"], "price": "100",
            "stock": 1, **kw}


def feed_row(seq, pid, op="UPSERT", reason="CONTENT", cu=T1):
    return {"seq": seq, "productId": pid, "op": op, "reason": reason,
            "contentUpdatedAt": cu if op == "UPSERT" else None}


class FakeStore:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.st = {"feed_cursor": None, "last_caught_up_at": None,
                   "last_resync_at": None, "resync_due_at": None}
        self.clock = NOW

    def state(self):
        return dict(self.st)

    def save_state(self, **f):
        self.st.update(f)

    def known(self):
        return {k: dict(v) for k, v in self.rows.items()}

    def get(self, i):
        return dict(self.rows[i]) if i in self.rows else None

    def put(self, i, tv, iv, h, cu, upd):
        old = self.rows.get(i, {})
        self.rows[i] = {"hash": h, "content_updated_at": cu or old.get("content_updated_at"),
                        "updated_at": upd or old.get("updated_at"), "sold_at": None}

    def touch(self, i, h=None, cu=None):
        r = self.rows[i]
        r["hash"] = h or r["hash"]
        r["content_updated_at"] = cu or r["content_updated_at"]
        r["sold_at"] = None

    def delete(self, ids):
        return sum(self.rows.pop(i, None) is not None for i in ids)

    def mark_sold(self, i):
        if i not in self.rows:
            return False
        self.rows[i]["sold_at"] = self.rows[i]["sold_at"] or self.clock
        return True

    def purge_sold(self, grace_days):
        gone = [i for i, r in self.rows.items()
                if r["sold_at"] and r["sold_at"] <= self.clock - timedelta(days=grace_days)]
        for i in gone:
            del self.rows[i]
        return len(gone)


class Backend:
    """Scripted backend: a feed (list of rows), a sellable catalogue (dict by id)."""

    def __init__(self, monkeypatch):
        self.rows: list[dict] = []
        self.sellable: dict[str, dict] = {}
        self.feed_calls: list[tuple[int, int]] = []
        self.by_ids_calls: list[list[str]] = []
        self.page_limit = None
        monkeypatch.setattr(indexer, "changes_page", self.changes)
        monkeypatch.setattr(indexer, "products_by_ids", self.by_ids)
        monkeypatch.setattr(indexer, "catalogue_page", self.catalogue)
        self.embeds = {"text": 0, "image": 0}
        monkeypatch.setattr(indexer, "embed_artwork_text", self._text)
        monkeypatch.setattr(indexer, "embed_artwork_image", self._image)

    def _text(self, *a, **k):
        self.embeds["text"] += 1
        return [0.1, 0.2]

    def _image(self, *a, **k):
        self.embeds["image"] += 1
        return [0.3, 0.4]

    def changes(self, since=0, limit=200):
        self.feed_calls.append((since, limit))
        limit = min(limit, self.page_limit or limit)
        ready = [r for r in self.rows if r["seq"] > since]
        page = ready[:limit]
        nxt = page[-1]["seq"] if page else since
        return {"items": page, "nextSince": nxt, "hasMore": len(ready) > len(page),
                "lag": {"seconds": 10, "withheld": 0}}

    def by_ids(self, ids):
        self.by_ids_calls.append(list(ids))
        return [self.sellable[i] for i in ids if i in self.sellable]

    def catalogue(self, after=None, limit=100):
        ids = sorted(i for i in self.sellable if after is None or i > after)[:limit]
        return [{**self.sellable[i], "updatedAt": T1} for i in ids]

    @property
    def embed_calls(self):
        return self.embeds["text"]


@pytest.fixture
def be(monkeypatch):
    monkeypatch.setattr(indexer, "INDEX_SOLD_GRACE_DAYS", 0)
    return Backend(monkeypatch)


@pytest.fixture
def store():
    return FakeStore()


def synced(store, be, now=NOW):
    """A store that has already done its first resync, with an empty feed."""
    indexer.sync(store, 100, now)
    be.embeds.update(text=0, image=0)
    return store


# -- first run, paging, cursor -------------------------------------------------

def test_first_run_resyncs_then_stores_the_feed_head(store, be):
    be.sellable = {"a": product("a"), "b": product("b")}
    be.rows = [feed_row(1, "a"), feed_row(2, "b")]
    stats = indexer.sync(store, 100, NOW)
    assert stats["resynced"] and stats["embedded"] == 2 and be.embed_calls == 2
    assert set(store.rows) == {"a", "b"}
    # the head was taken before the walk, so the feed rows are not replayed as embeds
    assert store.st["feed_cursor"] == 2
    assert stats["caught_up"] and store.st["last_caught_up_at"] == NOW
    assert store.st["resync_due_at"] == NOW + timedelta(hours=indexer.INDEX_RESYNC_HOURS)


def test_feed_is_paged_and_the_cursor_follows_each_page(store, be):
    synced(store, be)
    be.page_limit = 2
    be.sellable = {f"p{i}": product(f"p{i}") for i in range(5)}
    be.rows = [feed_row(10 + i, f"p{i}") for i in range(5)]
    stats = indexer.sync(store, 100, NOW)
    assert stats["embedded"] == 5 and stats["feed_rows"] == 5
    assert [c[0] for c in be.feed_calls[-3:]] == [0, 11, 13]   # loops while hasMore
    assert store.st["feed_cursor"] == 14
    be.feed_calls.clear()
    indexer.sync(store, 100, NOW)
    assert be.feed_calls == [(14, indexer._FEED_PAGE_SIZE)]   # resumes from the stored cursor


def test_cursor_is_not_advanced_when_the_backend_fails_mid_page(store, be, monkeypatch):
    synced(store, be)
    be.rows = [feed_row(1, "a")]
    be.sellable = {"a": product("a")}

    def boom(ids):
        raise indexer.BackendUnavailable("down")
    monkeypatch.setattr(indexer, "products_by_ids", boom)
    with pytest.raises(indexer.BackendUnavailable):
        indexer.sync(store, 100, NOW)
    assert store.st["feed_cursor"] == 0 and "a" not in store.rows


def test_by_ids_is_called_in_chunks_the_backend_accepts(store, be):
    synced(store, be)
    be.sellable = {f"p{i:03d}": product(f"p{i:03d}") for i in range(120)}
    be.rows = [feed_row(i + 1, f"p{i:03d}") for i in range(120)]
    indexer.sync(store, 500, NOW)
    assert max(len(c) for c in be.by_ids_calls) <= 50 and len(store.rows) == 120


# -- DELETE --------------------------------------------------------------------

@pytest.mark.parametrize("reason", [
    "SOLD", "ARCHIVED", "REJECTED", "UNPUBLISHED", "REVIEW", "SUBMITTED", "OUT_OF_STOCK",
    "DELETED", "SELLER_BANNED", "SELLER_DEACTIVATED", "SELLER_OFFLINE"])
def test_delete_removes_the_vector_for_every_reason_by_default(store, be, reason):
    be.sellable = {"a": product("a")}
    synced(store, be)
    assert "a" in store.rows
    be.rows = [feed_row(1, "a", "DELETE", reason)]
    stats = indexer.sync(store, 100, NOW)
    assert "a" not in store.rows
    assert stats["pruned"] == 1 and stats["pruned_reasons"] == {reason: 1}


def test_sold_is_kept_for_the_grace_window_then_purged(store, be, monkeypatch):
    monkeypatch.setattr(indexer, "INDEX_SOLD_GRACE_DAYS", 3)
    be.sellable = {"a": product("a")}
    synced(store, be)
    be.sellable = {}
    be.rows = [feed_row(1, "a", "DELETE", "SOLD")]
    stats = indexer.sync(store, 100, NOW)
    assert "a" in store.rows and store.rows["a"]["sold_at"] == NOW and stats["kept_sold"] == 1
    store.clock = NOW + timedelta(days=2)
    assert indexer.sync(store, 100, NOW + timedelta(days=2))["sold_purged"] == 0
    assert "a" in store.rows
    store.clock = NOW + timedelta(days=3, minutes=1)
    assert indexer.sync(store, 100, store.clock)["sold_purged"] == 1
    assert "a" not in store.rows


def test_grace_applies_only_to_sold(store, be, monkeypatch):
    monkeypatch.setattr(indexer, "INDEX_SOLD_GRACE_DAYS", 3)
    be.sellable = {"a": product("a")}
    synced(store, be)
    be.rows = [feed_row(1, "a", "DELETE", "ARCHIVED")]
    indexer.sync(store, 100, NOW)
    assert "a" not in store.rows


def test_relist_inside_the_grace_window_reuses_the_vector(store, be, monkeypatch):
    monkeypatch.setattr(indexer, "INDEX_SOLD_GRACE_DAYS", 3)
    be.sellable = {"a": product("a")}
    synced(store, be)
    be.rows = [feed_row(1, "a", "DELETE", "SOLD")]
    indexer.sync(store, 100, NOW)
    be.rows.append(feed_row(2, "a", "UPSERT", "RELISTED", T1))
    indexer.sync(store, 100, NOW)
    assert store.rows["a"]["sold_at"] is None and be.embed_calls == 0


def test_last_row_per_product_wins_inside_a_page(store, be):
    synced(store, be)
    be.sellable = {"a": product("a")}
    be.rows = [feed_row(1, "a"), feed_row(2, "a", "DELETE", "SOLD"), feed_row(3, "a", reason="RELISTED")]
    indexer.sync(store, 100, NOW)
    assert "a" in store.rows and be.embed_calls == 1 and be.by_ids_calls == [["a"]]


# -- UPSERT: when to pay -------------------------------------------------------

def test_upsert_with_the_same_content_updated_at_makes_no_embed_call(store, be):
    be.sellable = {"a": product("a")}
    be.rows = [feed_row(1, "a", cu=T1)]
    synced(store, be)
    store.rows["a"]["content_updated_at"] = indexer._parse_ts(T1)
    be.rows.append(feed_row(2, "a", reason="RESTOCKED", cu=T1))
    indexer.sync(store, 100, NOW)
    assert be.embed_calls == 0 and be.embeds["image"] == 0


def test_a_newer_content_updated_at_with_changed_content_embeds_exactly_once(store, be):
    be.sellable = {"a": product("a")}
    synced(store, be)
    store.rows["a"]["content_updated_at"] = indexer._parse_ts(T1)
    be.sellable = {"a": product("a", title="A new title")}
    be.rows = [feed_row(1, "a", cu=T2)]
    indexer.sync(store, 100, NOW)
    assert be.embeds == {"text": 1, "image": 1}
    assert store.rows["a"]["content_updated_at"] == indexer._parse_ts(T2)
    indexer.sync(store, 100, NOW)       # re-reading the window changes nothing
    be.rows.append(feed_row(2, "a", reason="RESTOCKED", cu=T2))
    indexer.sync(store, 100, NOW)
    assert be.embeds == {"text": 1, "image": 1}


def test_a_newer_stamp_with_an_identical_hash_is_recorded_without_embedding(store, be):
    be.sellable = {"a": product("a")}
    synced(store, be)
    be.rows = [feed_row(1, "a", cu=T2)]    # e.g. only tags changed on the backend
    indexer.sync(store, 100, NOW)
    assert be.embed_calls == 0
    assert store.rows["a"]["content_updated_at"] == indexer._parse_ts(T2)


def test_an_older_stamp_never_re_embeds(store, be):
    be.sellable = {"a": product("a")}
    synced(store, be)
    store.rows["a"]["content_updated_at"] = indexer._parse_ts(T2)
    be.sellable = {"a": product("a", title="changed")}
    be.rows = [feed_row(1, "a", cu=T1)]
    indexer.sync(store, 100, NOW)
    assert be.embed_calls == 0


def test_a_stock_decrement_costs_zero_embeddings(store, be):
    """The backend writes no feed row for a sale that leaves stock above 0 (and a
    price edit), so the pass sees an empty feed: no by-ids call, no Gemini call."""
    be.sellable = {"a": product("a", stock=5)}
    synced(store, be)
    be.sellable = {"a": product("a", stock=4, price="120")}
    stats = indexer.sync(store, 100, NOW)
    assert stats["feed_rows"] == 0 and stats["embedded"] == 0
    assert be.embeds == {"text": 0, "image": 0} and be.by_ids_calls == []


def test_an_upsert_missing_from_by_ids_is_treated_as_a_delete(store, be):
    be.sellable = {"a": product("a")}
    synced(store, be)
    be.sellable = {}                        # seller hidden between the feed row and now
    be.rows = [feed_row(1, "a", reason="SELLER_RESTORED")]
    stats = indexer.sync(store, 100, NOW)
    assert "a" not in store.rows and stats["pruned_reasons"] == {"NOT_SELLABLE": 1}


def test_a_product_returning_after_a_prune_is_embedded_again(store, be):
    be.sellable = {"a": product("a")}
    synced(store, be)
    be.rows = [feed_row(1, "a", "DELETE", "ARCHIVED")]
    indexer.sync(store, 100, NOW)
    be.rows.append(feed_row(2, "a", reason="RESTORED"))
    indexer.sync(store, 100, NOW)
    assert "a" in store.rows and be.embed_calls == 1


# -- cost cap and failures -----------------------------------------------------

def test_the_per_run_cap_stops_the_cursor_at_the_first_unprocessed_row(store, be):
    synced(store, be)
    be.sellable = {f"p{i}": product(f"p{i}") for i in range(5)}
    be.rows = [feed_row(i + 1, f"p{i}") for i in range(5)]
    stats = indexer.sync(store, 2, NOW)
    assert stats["embedded"] == 2 and not stats["caught_up"]
    assert store.st["feed_cursor"] == 2          # rows 3..5 are re-read next pass
    assert indexer.sync(store, 2, NOW)["embedded"] == 2
    assert indexer.sync(store, 2, NOW)["embedded"] == 1
    assert set(store.rows) == {f"p{i}" for i in range(5)} and store.st["feed_cursor"] == 5


def test_deletes_are_free_when_the_embedding_cap_is_spent(store, be):
    be.sellable = {"a": product("a")}
    synced(store, be)
    be.sellable = {"b": product("b"), "c": product("c")}
    be.rows = [feed_row(1, "b"), feed_row(2, "c"), feed_row(3, "a", "DELETE", "SOLD")]
    indexer.sync(store, 1, NOW)
    assert "a" not in store.rows


def test_a_failed_embedding_is_counted_and_schedules_an_early_resync(store, be, monkeypatch):
    synced(store, be)
    be.sellable = {"a": product("a"), "b": product("b")}
    store.st["resync_due_at"] = NOW + timedelta(hours=24)
    be.rows = [feed_row(1, "a"), feed_row(2, "b")]
    calls = []

    def flaky(title, details=""):
        calls.append(title)
        if title == "Title a":
            raise RuntimeError("quota")
        return [0.1]
    monkeypatch.setattr(indexer, "embed_artwork_text", flaky)
    stats = indexer.sync(store, 100, NOW)
    assert stats["failed"] == 1 and stats["embedded"] == 1 and "b" in store.rows
    assert store.st["resync_due_at"] == NOW + indexer._RESYNC_RETRY
    # the retry resync embeds what the cursor already moved past
    monkeypatch.setattr(indexer, "embed_artwork_text", be._text)
    later = NOW + indexer._RESYNC_RETRY
    stats = indexer.sync(store, 100, later)
    assert stats["resynced"] and "a" in store.rows


# -- resync --------------------------------------------------------------------

def test_resync_prunes_vectors_that_left_the_catalogue(store, be):
    be.sellable = {"a": product("a"), "b": product("b")}
    synced(store, be)
    be.sellable = {"a": product("a")}
    later = NOW + timedelta(hours=indexer.INDEX_RESYNC_HOURS, minutes=1)
    stats = indexer.sync(store, 100, later)
    assert stats["resynced"] and set(store.rows) == {"a"}
    assert stats["pruned_reasons"] == {"RESYNC": 1} and be.embed_calls == 0


def test_resync_never_prunes_against_an_empty_catalogue(store, be):
    be.sellable = {"a": product("a")}
    synced(store, be)
    be.sellable = {}
    indexer.sync(store, 100, NOW + timedelta(hours=25))
    assert "a" in store.rows


def test_resync_skips_unchanged_products_and_adopts_hashes_for_legacy_rows(store, be):
    be.sellable = {"a": product("a"), "b": product("b")}
    legacy_current = indexer._parse_ts(T1)
    store.rows = {   # embedded before A04: no hash, only source_updated_at
        "a": {"hash": None, "content_updated_at": None, "updated_at": legacy_current, "sold_at": None},
        "b": {"hash": None, "content_updated_at": None, "updated_at": legacy_current - timedelta(days=1),
              "sold_at": None},
    }
    stats = indexer.sync(store, 100, NOW)
    assert stats["embedded"] == 1                      # only b was really stale
    assert store.rows["a"]["hash"] == indexer.content_hash(be.sellable["a"])
    be.embeds.update(text=0, image=0)
    indexer.sync(store, 100, NOW + timedelta(hours=25))
    assert be.embed_calls == 0                          # nothing changed: nothing paid


def test_resync_cap_leaves_the_walk_unfinished_and_unpruned(store, be):
    be.sellable = {f"p{i}": product(f"p{i}") for i in range(4)}
    stats = indexer.sync(store, 2, NOW)
    assert stats["embedded"] == 2 and not stats["caught_up"]
    assert store.st["feed_cursor"] is None and store.st["resync_due_at"] == NOW
    be.sellable.pop("p0")
    store.rows["gone"] = {"hash": "x", "content_updated_at": None, "updated_at": None, "sold_at": None}
    while store.st["feed_cursor"] is None:
        indexer.sync(store, 2, NOW)
    assert set(store.rows) == {"p1", "p2", "p3"}


def test_a_cursor_older_than_the_stale_window_forces_a_resync(store, be):
    be.sellable = {"a": product("a"), "b": product("b")}
    synced(store, be)
    be.sellable = {"a": product("a")}
    be.rows = [feed_row(1, "a")]
    late = NOW + timedelta(days=indexer.INDEX_FEED_STALE_DAYS + 1)
    stats = indexer.sync(store, 100, late)
    assert stats["resynced"] and "b" not in store.rows


def test_a_feed_that_goes_backwards_resets_the_cursor_and_resyncs(store, be, monkeypatch):
    synced(store, be)
    store.st["feed_cursor"] = 500
    store.st["resync_due_at"] = NOW + timedelta(hours=24)
    monkeypatch.setattr(indexer, "changes_page",
                        lambda since=0, limit=200: {"items": [], "nextSince": 3, "hasMore": False,
                                                    "lag": {"seconds": 10, "withheld": 0}})
    stats = indexer.sync(store, 100, NOW)
    assert not stats["caught_up"]
    assert store.st["feed_cursor"] is None and store.st["resync_due_at"] == NOW


# -- lag metric ----------------------------------------------------------------

def _sample(name):
    from app.observability import REGISTRY
    return REGISTRY.get_sample_value(name)


def test_index_lag_only_resets_when_caught_up_with_the_feed():
    from app import observability as obs
    obs.INDEX_LAST_SUCCESS.set(0)
    obs.record_index_run({"needs": 0, "embedded": 0, "caught_up": False}, failed=False)
    assert obs.INDEX_LAST_SUCCESS._value.get() == 0       # capped pass: lag keeps growing
    obs.record_index_run({"needs": 0, "embedded": 0, "caught_up": True,
                          "pruned_reasons": {"SOLD": 2}}, failed=False)
    assert _sample("koli_agent_index_lag_seconds") < 5
    assert obs.REGISTRY.get_sample_value("koli_agent_index_pruned_total", {"reason": "SOLD"}) >= 2


# -- PgStore SQL shape (no database needed) -----------------------------------

class _Conn:
    """Records the statements PgStore sends, like a connection would receive them."""

    def __init__(self):
        self.sent = []
        self.commits = 0

    def execute(self, stmt, params=None):
        self.sent.append((stmt, params))

    def commit(self):
        self.commits += 1


def test_save_state_updates_only_the_named_columns_with_bound_values():
    from sqlalchemy.dialects import postgresql
    conn = _Conn()
    cursor = 7
    indexer.PgStore(conn).save_state(feed_cursor=cursor, resync_due_at=NOW)
    assert conn.commits == 1 and len(conn.sent) == 2
    compiled = conn.sent[1][0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert sql.startswith("UPDATE index_state SET feed_cursor=%(feed_cursor)s, "
                          "resync_due_at=%(resync_due_at)s, updated_at=now() WHERE")
    assert "last_caught_up_at" not in sql
    assert compiled.params["feed_cursor"] == cursor and compiled.params["resync_due_at"] == NOW


def test_save_state_refuses_unknown_columns_and_does_nothing_for_none():
    conn = _Conn()
    store = indexer.PgStore(conn)
    with pytest.raises(ValueError):
        store.save_state(feed_cursor=1, updated_at=NOW)   # updated_at is not settable
    with pytest.raises(ValueError):
        store.save_state(**{"feed_cursor = 1; DROP TABLE x; --": 1})
    store.save_state()
    assert conn.sent == [] and conn.commits == 0


# -- the SQL, against a real database (CI, or a local pgvector) ------------------

def _pg():
    from sqlalchemy import text
    from app import database
    try:
        with database.engine.connect() as c:
            c.execute(text("SELECT 1"))
            ok = c.execute(text("SELECT to_regclass('agent.index_state')")).scalar()
        return database.engine if ok else None
    except Exception:
        return None


def test_pgstore_round_trip():
    from sqlalchemy import text
    eng = _pg()
    if eng is None:
        pytest.skip("no database migrated to a04d7b3e9c52")
    with eng.connect() as conn:
        store = indexer.PgStore(conn)
        conn.execute(text("DELETE FROM artwork_embeddings WHERE artwork_id LIKE 'ittest-%'"))
        conn.commit()
        try:
            store.save_state(feed_cursor=42, resync_due_at=NOW)
            assert store.state()["feed_cursor"] == 42
            vec = indexer._vec([0.5] * 3072)
            store.put("ittest-1", vec, None, "a" * 64, NOW, NOW)
            row = store.get("ittest-1")
            assert row["hash"] == "a" * 64 and row["content_updated_at"] == NOW
            store.touch("ittest-1", "b" * 64, None)
            assert store.get("ittest-1")["hash"] == "b" * 64 and \
                store.get("ittest-1")["content_updated_at"] == NOW
            assert store.mark_sold("ittest-1") and store.get("ittest-1")["sold_at"] is not None
            assert store.purge_sold(3) == 0
            assert store.purge_sold(0) >= 1 and store.get("ittest-1") is None
            store.put("ittest-2", vec, vec, "c" * 64, None, None)
            assert store.delete(["ittest-2", "ittest-nope"]) == 1
            with pytest.raises(ValueError):
                store.save_state(bogus=1)
        finally:
            conn.rollback()
            conn.execute(text("DELETE FROM artwork_embeddings WHERE artwork_id LIKE 'ittest-%'"))
            conn.execute(text("UPDATE index_state SET feed_cursor = NULL, resync_due_at = NULL"))
            conn.commit()
