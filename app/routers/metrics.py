import hmac
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import config
from app.observability import REGISTRY

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
def metrics(authorization: Optional[str] = Header(default=None)):
    """Prometheus metrics. Closed by default: with METRICS_TOKEN unset the route
    does not exist (404), so a production deploy exposes nothing until an
    operator opts in. Scrape with `Authorization: Bearer <METRICS_TOKEN>`.
    Keep it off any public route; it is for the docker network only.
    """
    token = config.METRICS_TOKEN
    if not token:
        raise HTTPException(status_code=404, detail="Not Found")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(
            supplied.strip().encode(), token.encode()):
        raise HTTPException(status_code=401, detail="Invalid metrics token",
                            headers={"WWW-Authenticate": "Bearer"})
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
