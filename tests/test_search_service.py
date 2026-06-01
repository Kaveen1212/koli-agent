from app.services.search_service import search_artworks, get_product_by_id


def test_search_artworks_returns_list():
    results = search_artworks("calm earthy")
    assert isinstance(results, list), "search_artworks must return a list"


def test_search_artworks_row_has_required_keys():
    results = search_artworks("art")
    if results:
        row = results[0]
        for key in ("id", "title", "price", "images"):
            assert key in row, f"Missing key '{key}' in search result"


def test_get_product_by_id_returns_empty_for_unknown():
    result = get_product_by_id("nonexistent-id-000")
    assert result == {}, "Unknown ID must return empty dict"
