from langchain_core.tools import tool
from app.services.search_service import search_artworks, get_product_by_id
from app.database import SessionLocal
from sqlalchemy import text


@tool
def search_art(query: str, category: str = "", price_max: float = 0) -> list:
    """Search the art catalog by style, color, mood, or description.
    Use when the customer describes what kind of art they want.
    Finds art by meaning — 'calm earthy tones' works, not just exact words.
    """
    results = search_artworks(query, category=category, price_max=price_max)
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "price": str(r["price"]),
            "medium": r.get("medium", ""),
            "size": r.get("size", ""),
            "image_url": r.get("images", [])[0] if r.get("images") else "",
        }
        for r in results
    ]


@tool
def filter_by_category(category: str, limit: int = 10) -> list:
    """Filter the catalog by an exact category — use when the customer says
    'show me only prints', 'only home décor', 'only paintings', etc.
    Returns products without vector search — faster than search_art for category-only requests.
    """
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT id, title, price, medium, size, category, images
            FROM "Product"
            WHERE status = 'ONLINE'
              AND "isDeleted" = false
              AND stock > 0
              AND category ILIKE :category
            ORDER BY "createdAt" DESC
            LIMIT :limit
        """), {"category": f"%{category}%", "limit": limit})
        return [
            {
                "id":        r["id"],
                "title":     r["title"],
                "price":     str(r["price"]),
                "medium":    r.get("medium", ""),
                "size":      r.get("size", ""),
                "category":  r.get("category", ""),
                "image_url": r.get("images", [])[0] if r.get("images") else "",
            }
            for r in rows.mappings().all()
        ]
    finally:
        db.close()


@tool
def find_artwork_by_title(title: str) -> dict:
    """Find an artwork by its title — use when the customer names a specific artwork
    they saw earlier and you need its ID to call visualize_on_wall.
    Does a direct title match, no vector search needed.
    """
    db = SessionLocal()
    try:
        row = db.execute(text("""
            SELECT id, title, price, medium, size, category, images
            FROM "Product"
            WHERE title ILIKE :title
              AND "isDeleted" = false
            LIMIT 1
        """), {"title": f"%{title}%"}).mappings().first()
        if not row:
            return {"error": f"No artwork found with title containing '{title}'"}
        return {
            "id":        row["id"],
            "title":     row["title"],
            "price":     str(row["price"]),
            "medium":    row.get("medium", ""),
            "size":      row.get("size", ""),
            "image_url": row.get("images", [])[0] if row.get("images") else "",
        }
    finally:
        db.close()


@tool
def get_item_details(artwork_id: str) -> dict:
    """Get full details for one artwork by its ID.
    Use when the customer asks for price, size, medium, or wants to buy.
    """
    row = get_product_by_id(artwork_id)
    if not row:
        return {"error": f"Artwork {artwork_id} not found."}
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row.get("description", ""),
        "price": str(row["price"]),
        "medium": row.get("medium", ""),
        "size": row.get("size", ""),
        "images": row.get("images", []),
    }
