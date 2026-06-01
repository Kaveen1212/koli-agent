import httpx
from google import genai
from google.genai import types
from app.config import GEMINI_API_KEY, EMBEDDING_MODEL, EMBEDDING_DIM
from app.services.gemini_retry import with_retry

client = genai.Client(api_key=GEMINI_API_KEY)

_DIM_CONFIG = types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM)


def embed_query(query: str) -> list[float]:
    """Embed a customer search query.
    Uses the search-result task prefix so it matches artwork document embeddings.
    """
    formatted = f"task: search result | query: {query}"
    result = with_retry(
        client.models.embed_content,
        model=EMBEDDING_MODEL,
        contents=formatted,
        config=_DIM_CONFIG,
    )
    return result.embeddings[0].values


def embed_artwork_text(title: str, description: str) -> list[float]:
    """Embed an artwork's title + description for storage in artwork_embeddings.
    Uses the document prefix so it shares a space with search queries.
    """
    formatted = f"title: {title} | text: {description}"
    result = with_retry(
        client.models.embed_content,
        model=EMBEDDING_MODEL,
        contents=formatted,
        config=_DIM_CONFIG,
    )
    return result.embeddings[0].values


def embed_artwork_image(image_url: str) -> list[float]:
    """Embed an artwork image for storage in artwork_embeddings.image_vector.
    Uses the SAME model as text embeddings so both live in one vector space
    (gemini-embedding-2 is multimodal). Required for search-by-room-photo.
    """
    image_bytes = httpx.get(image_url, timeout=30, follow_redirects=True).content
    result = with_retry(
        client.models.embed_content,
        model=EMBEDDING_MODEL,
        contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")],
        config=_DIM_CONFIG,
    )
    return result.embeddings[0].values
