"""
Embed every sellable product that isn't indexed yet (or changed since), now.

The agent already does this by itself every INDEX_INTERVAL_SECONDS (see
app/services/indexer.py); run this to catch up immediately, e.g. after a bulk
import. It goes through the backend API like the background indexer — it no
longer reads Prisma's "Product" table directly, which also indexed drafts and
unapproved products.

Usage: uv run python scripts/ingest_embeddings.py
   or: docker exec koli-agent python scripts/ingest_embeddings.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.indexer import index_catalog


if __name__ == "__main__":
    stats = index_catalog(max_embeds=100_000)
    print(stats)
