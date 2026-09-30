import os
import json
import uuid
import base64
import logging
import httpx
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages import messages_from_dict, messages_to_dict
from langgraph.graph import StateGraph, MessagesState, END
from langgraph.prebuilt import ToolNode, tools_condition
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import TOOLS
from app.config import GEMINI_API_KEY, AGENT_MODEL, MAX_HISTORY_TURNS, PUBLIC_BASE_URL
from app.database import SessionLocal
from sqlalchemy import text

os.environ["GOOGLE_API_KEY"] = GEMINI_API_KEY

logger = logging.getLogger(__name__)

model = ChatGoogleGenerativeAI(
    model=AGENT_MODEL,
    temperature=0.7,
    max_retries=3,  # auto-retry transient 503/429 overload errors
)
model_with_tools = model.bind_tools(TOOLS)


def _load_image_bytes(url: str) -> bytes:
    if "localhost" in url or "127.0.0.1" in url or url.startswith(PUBLIC_BASE_URL):
        local_path = url.split("/static/")[1].split("?")[0]
        with open(os.path.join("static", local_path), "rb") as f:
            return f.read()
    return httpx.get(url, timeout=30, follow_redirects=True).content


def _prepare_for_gemini(messages: list) -> list:
    """Send the actual image bytes ONLY for the most recent human message; older
    image references are collapsed to a text note. This avoids re-uploading the
    same photo to Gemini on every turn (token/cost waste) and keeps traces small.
    """
    last_human_idx = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
        default=-1,
    )
    result = []
    for i, msg in enumerate(messages):
        if isinstance(msg, HumanMessage) and isinstance(msg.content, list):
            new_parts = []
            for part in msg.content:
                is_img = isinstance(part, dict) and part.get("type") == "image_url"
                if is_img and i == last_human_idx:
                    url = part["image_url"]["url"]
                    if not url.startswith("data:"):
                        b64 = base64.b64encode(_load_image_bytes(url)).decode()
                        url = f"data:image/jpeg;base64,{b64}"
                    new_parts.append({"type": "image_url", "image_url": {"url": url}})
                elif is_img:
                    new_parts.append({"type": "text", "text": "[earlier uploaded image]"})
                else:
                    new_parts.append(part)
            result.append(HumanMessage(content=new_parts))
        else:
            result.append(msg)
    return result


def _trim_history(history: list) -> list:
    """Keep the most recent messages, starting at a HumanMessage boundary so the
    model never receives an orphaned tool/AI message.
    """
    max_messages = MAX_HISTORY_TURNS * 4  # room for tool calls per turn
    if len(history) <= max_messages:
        return history
    trimmed = history[-max_messages:]
    for i, m in enumerate(trimmed):
        if isinstance(m, HumanMessage):
            return trimmed[i:]
    return trimmed


def agent_node(state: MessagesState):
    """The agent node — Gemini reads the messages and decides what to do."""
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    gemini_messages = _prepare_for_gemini(messages)
    response = model_with_tools.invoke(gemini_messages)
    return {"messages": [response]}

tools_node = ToolNode(TOOLS)

builder = StateGraph(MessagesState)
builder.add_node("agent", agent_node)
builder.add_node("tools", tools_node)
builder.set_entry_point("agent")
builder.add_conditional_edges("agent", tools_condition)
builder.add_edge("tools", "agent")
graph = builder.compile()


def _session_id(user_id: str) -> str:
    """One persistent session row per user."""
    return f"session-{user_id}"


def _load_history(user_id: str) -> list:
    """Load conversation history from DB. Returns empty list if no session exists."""
    db = SessionLocal()
    try:
        session = db.execute(
            text('SELECT messages_json FROM chat_sessions WHERE id = :id'),
            {"id": _session_id(user_id)},
        ).mappings().first()
        if session and session["messages_json"]:
            return messages_from_dict(json.loads(session["messages_json"]))
        return []
    finally:
        db.close()


def _save_history(user_id: str, history: list) -> None:
    """Atomically upsert conversation history (one row per user)."""
    db = SessionLocal()
    try:
        messages_json = json.dumps(messages_to_dict(_trim_history(history)))
        db.execute(text("""
            INSERT INTO chat_sessions (id, user_id, messages_json)
            VALUES (:id, :uid, :msgs)
            ON CONFLICT (id) DO UPDATE
            SET messages_json = EXCLUDED.messages_json,
                updated_at = now()
        """), {"id": _session_id(user_id), "uid": user_id, "msgs": messages_json})
        db.commit()
    finally:
        db.close()


_NUDGE = HumanMessage(content=(
    "(System note: your last turn produced no text for the customer. Reply to them now "
    "in plain text, using the conversation and tool results above. If nothing was found, "
    "say so honestly and ask one short question about what they are looking for.)"
))

_FALLBACK_REPLY = (
    "I couldn't find anything matching that just yet — our collection is still growing. "
    "Could you tell me a bit more about what you're looking for, like the style, colours, "
    "size or budget?"
)


def _text_of(msg) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        content = " ".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("text"))
    return (content or "").strip()


def _recover_reply(messages: list) -> str:
    """Gemini sometimes ends a turn with no text at all — typically right after a
    tool came back empty — and the backend turns an empty reply into an error
    for the customer. Ask once more with tool calls disabled, then fall back to
    a fixed answer rather than return nothing.
    """
    logger.warning("Model returned an empty reply; asking again without tools")
    convo = [m for m in messages
             if not (isinstance(m, AIMessage) and not m.tool_calls and not _text_of(m))]
    try:
        text_only = model.bind_tools(TOOLS, tool_choice="none")
        response = text_only.invoke(_prepare_for_gemini(
            [SystemMessage(content=SYSTEM_PROMPT)] + convo + [_NUDGE]))
        return _text_of(response) or _FALLBACK_REPLY
    except Exception:
        logger.exception("Empty-reply recovery failed; using the fixed reply")
        return _FALLBACK_REPLY


def process_message(user_id: str, message: str, image_url: str = None) -> str:
    history = _load_history(user_id)

    if image_url and image_url.startswith(("http://", "https://")):
        text_with_url = (
            f"{message}\n\n"
            f"[The customer's uploaded room photo URL is: {image_url} — "
            f"use this exact URL when calling analyze_room, visualize_on_wall, or stage_decor.]"
        )
        user_msg = HumanMessage(content=[
            {"type": "text",      "text": text_with_url},
            {"type": "image_url", "image_url": {"url": image_url}},
        ])
    else:
        user_msg = HumanMessage(content=message)

    history.append(user_msg)

    # Tools read the caller's identity from the config (see tools/_identity.py),
    # not from their own arguments, which the model — and so the visitor — picks.
    result = graph.invoke({"messages": history},
                          config={"configurable": {"user_id": user_id}})
    last_msg = result["messages"][-1]
    reply = _text_of(last_msg)
    if not reply:
        reply = _recover_reply(result["messages"])
        last_msg = AIMessage(content=reply)
    history.append(last_msg)

    _save_history(user_id, history)
    return reply