import hmac

from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Header, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from typing import Optional

from app.agent.orchestrator import process_message
from app.config import INTERNAL_SERVICE_TOKEN
from app.observability import request_id_from

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRequest(BaseModel):
    # The backend's SendChatDto already bounds these; the agent re-checks
    # rather than trusting the caller, and refuses fields it does not know.
    model_config = ConfigDict(extra="forbid")

    user_id:   str = Field(min_length=1, max_length=128)
    message:   str = Field(max_length=4000)
    image_url: Optional[str] = Field(default=None, max_length=1000)


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
    response: Response,
    x_service_token: Optional[str] = Header(default=None, alias="X-Service-Token"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
):
    _require_service_token(x_service_token)

    # The backend forwards its request id so one id follows a chat turn across
    # both services; without one (or with a malformed one) the agent mints its own.
    request_id = request_id_from(x_request_id)
    response.headers["X-Request-ID"] = request_id

    # process_message does blocking DB + Gemini I/O; run it off the event loop
    # so one slow request doesn't stall all other users.
    reply = await run_in_threadpool(
        process_message,
        request.user_id,
        request.message,
        request.image_url,
        request_id,
    )
    return {"response": reply}
