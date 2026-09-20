import hmac

from pydantic import BaseModel
from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from typing import Optional

from app.agent.orchestrator import process_message
from app.config import INTERNAL_SERVICE_TOKEN

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRequest(BaseModel):
    user_id:   str
    message:   str
    image_url: Optional[str] = None


def _require_service_token(token: Optional[str]) -> None:
    """Only the koli-ART backend may drive the agent.

    Browsers reach the assistant through the backend's POST /v1/chat, which
    applies auth and rate limiting. Leaving this endpoint open would let anyone
    spend Gemini credits directly, bypassing both.

    Unset token = closed, not open. An empty secret must never authenticate.
    """
    if not INTERNAL_SERVICE_TOKEN:
        raise HTTPException(status_code=503, detail="Service authentication is not configured")
    if not token or not hmac.compare_digest(token, INTERNAL_SERVICE_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid service token")


@router.post("/message")
async def send_message(
    request: AgentRequest,
    x_service_token: Optional[str] = Header(default=None, alias="X-Service-Token"),
):
    _require_service_token(x_service_token)

    # process_message does blocking DB + Gemini I/O; run it off the event loop
    # so one slow request doesn't stall all other users.
    response = await run_in_threadpool(
        process_message,
        request.user_id,
        request.message,
        request.image_url,
    )
    return {"response": response}
