from app.agent.tools.artwork import search_art, get_item_details, filter_by_category


def _cards(result):
    """Search tools return a list of cards, or a dict explaining an empty result."""
    if isinstance(result, dict):
        assert result.get("results") == [] and "note" in result
        return []
    assert isinstance(result, list)
    return result


def test_search_art_returns_cards_or_explanation():
    _cards(search_art.invoke({"query": "abstract"}))


def test_search_art_row_shape():
    for row in _cards(search_art.invoke({"query": "art"}))[:1]:
        for key in ("id", "title", "price", "image_url"):
            assert key in row, f"Missing key '{key}'"


def test_get_item_details_unknown_id():
    result = get_item_details.invoke({"artwork_id": "nonexistent-000"})
    assert "error" in result, "Unknown artwork_id must return error key"


def test_filter_by_category_returns_cards_or_explanation():
    _cards(filter_by_category.invoke({"category": "art"}))
