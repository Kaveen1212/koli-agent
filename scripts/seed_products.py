"""
Inserts test products into the koli_art database for agent testing.
Usage: uv run python scripts/seed_products.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from app.database import SessionLocal
import uuid

TEST_PRODUCTS = [
    {
        "title": "Ocean Calm",
        "description": "A serene abstract painting in deep blues and teals, evoking the stillness of the ocean at dawn.",
        "price": "180.00",
        "medium": "Acrylic",
        "size": "24x24 inches",
        "category": "Abstract",
        "images": '{"https://images.unsplash.com/photo-1541961017774-22349e4a1262?w=800"}',
        "status": "ONLINE",
    },
    {
        "title": "Terracotta Warmth",
        "description": "Earthy tones of terracotta, ochre, and warm beige. Minimalist composition with bold textures.",
        "price": "240.00",
        "medium": "Oil",
        "size": "30x40 inches",
        "category": "Abstract",
        "images": '{"https://images.unsplash.com/photo-1578662996442-48f60103fc96?w=800"}',
        "status": "ONLINE",
    },
    {
        "title": "Forest Path",
        "description": "Lush green forest scene with dappled light filtering through tall trees. Impressionist style.",
        "price": "320.00",
        "medium": "Oil",
        "size": "36x48 inches",
        "category": "Landscape",
        "images": '{"https://images.unsplash.com/photo-1448375240586-882707db888b?w=800"}',
        "status": "ONLINE",
    },
    {
        "title": "City Lights",
        "description": "Modern cityscape at night. Neon reflections on wet streets, bold colors and sharp lines.",
        "price": "150.00",
        "medium": "Digital Print",
        "size": "20x30 inches",
        "category": "Urban",
        "images": '{"https://images.unsplash.com/photo-1477959858617-67f85cf4f1df?w=800"}',
        "status": "ONLINE",
    },
    {
        "title": "Minimal Lines",
        "description": "Black and white geometric composition. Clean lines, negative space, and perfect symmetry.",
        "price": "120.00",
        "medium": "Ink on Paper",
        "size": "16x20 inches",
        "category": "Minimalist",
        "images": '{"https://images.unsplash.com/photo-1605106702734-205df224ecce?w=800"}',
        "status": "ONLINE",
    },
    {
        "title": "Sunset Bloom",
        "description": "Vibrant floral arrangement in warm sunset tones. Pink, orange, and gold on deep burgundy.",
        "price": "195.00",
        "medium": "Watercolour",
        "size": "18x24 inches",
        "category": "Botanical",
        "images": '{"https://images.unsplash.com/photo-1490750967868-88df5691cc6d?w=800"}',
        "status": "ONLINE",
    },
]

SELLER_ID = "seed-seller-001"


def seed():
    db = SessionLocal()
    try:
        # Create a minimal seller user if none exists
        existing = db.execute(
            text('SELECT id FROM "User" WHERE id = :id'),
            {"id": SELLER_ID}
        ).first()

        if not existing:
            db.execute(text("""
                INSERT INTO "User" (id, email, username, role, "isVerified", "isActive", "createdAt", "updatedAt")
                VALUES (:id, :email, :username, 'SELLER', true, true, now(), now())
                ON CONFLICT DO NOTHING
            """), {
                "id": SELLER_ID,
                "email": "seed@artnuss.com",
                "username": "seed_artist",
            })

        count = 0
        for p in TEST_PRODUCTS:
            product_id = str(uuid.uuid4())
            db.execute(text("""
                INSERT INTO "Product" (id, "sellerId", title, description, price, images,
                                       status, stock, medium, size, category, "frameColors",
                                       views, "isDeleted", "createdAt", "updatedAt")
                VALUES (:id, :seller_id, :title, :desc, :price, :images,
                        :status, 1, :medium, :size, :category, '{}',
                        0, false, now(), now())
            """), {
                "id":        product_id,
                "seller_id": SELLER_ID,
                "title":     p["title"],
                "desc":      p["description"],
                "price":     p["price"],
                "images":    p["images"],
                "status":    p["status"],
                "medium":    p["medium"],
                "size":      p["size"],
                "category":  p["category"],
            })
            count += 1
            print(f"Added: {p['title']}")

        db.commit()
        print(f"\nDone — {count} products added.")
        print("Now run: uv run python scripts/ingest_embeddings.py")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
