from pydantic import BaseModel
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from typing import Optional
from app.agent.orchestrator import process_message

router = APIRouter(prefix="/agent", tags=["agent"])

class AgentRequest(BaseModel):
    user_id:   str
    message:   str
    image_url: Optional[str] = None

@router.post("/message")
async def send_message(request: AgentRequest):
    # process_message does blocking DB + Gemini I/O; run it off the event loop
    # so one slow request doesn't stall all other users.
    response = await run_in_threadpool(
        process_message,
        request.user_id,
        request.message,
        request.image_url,
    )
    return {"response": response}
