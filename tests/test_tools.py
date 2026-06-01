from app.agent.tools.artwork import search_art, get_item_details, filter_by_category


def test_search_art_returns_list():
    result = search_art.invoke({"query": "abstract"})
    assert isinstance(result, list)


def test_search_art_row_shape():
    result = search_art.invoke({"query": "art"})
    if result:
        row = result[0]
        for key in ("id", "title", "price", "image_url"):
            assert key in row, f"Missing key '{key}'"


def test_get_item_details_unknown_id():
    result = get_item_details.invoke({"artwork_id": "nonexistent-000"})
    assert "error" in result, "Unknown artwork_id must return error key"


def test_filter_by_category_returns_list():
    result = filter_by_category.invoke({"category": "art"})
    assert isinstance(result, list)
