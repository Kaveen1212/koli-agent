import hmac
from typing import Optional

from fastapi import Header, HTTPException

from app.config import INTERNAL_SERVICE_TOKEN


def require_service_token(
    x_service_token: Optional[str] = Header(default=None, alias="X-Service-Token"),
) -> None:
    """Shared guard: only the koli-ART backend may reach internal agent routes.

    Browsers must go through the backend (which applies auth + rate limiting and
    holds the service token server-side). Leaving these endpoints open lets
    anyone spend Gemini credits directly.

    Unset secret = closed, not open — an empty token must never authenticate.
    Use as an APIRouter/endpoint dependency: dependencies=[Depends(require_service_token)].
    """
    if not INTERNAL_SERVICE_TOKEN:
        raise HTTPException(
            status_code=503, detail="Service authentication is not configured"
        )
    if not x_service_token or not hmac.compare_digest(
        x_service_token, INTERNAL_SERVICE_TOKEN
    ):
        raise HTTPException(status_code=401, detail="Invalid service token")
