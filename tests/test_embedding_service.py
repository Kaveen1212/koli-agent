from app.services.embedding_service import embed_query


def test_embed_query_returns_3072_floats():
    vector = embed_query("blue abstract")
    assert isinstance(vector, list), "embed_query must return a list"
    assert len(vector) == 3072, f"Expected 3072 dimensions, got {len(vector)}"
    assert all(isinstance(v, float) for v in vector), "All values must be floats"
