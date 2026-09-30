"""Who is asking — read from the run config, never from a tool argument.

process_message puts the backend-supplied user_id into the graph's config.
Tool arguments are chosen by the model, so anything that gates paid work on
identity must not trust them: a visitor could simply tell the model to pass
someone else's id, or a fresh one to reset their daily limit.
"""
from langchain_core.runnables import RunnableConfig

SIGN_IN_REQUIRED = {
    "error": "sign_in_required",
    "message": (
        "Previews are only available to signed-in customers. Invite the customer "
        "to sign in (Login, top right of the page) and then ask again."
    ),
}


def signed_in_user_id(config: RunnableConfig | None) -> str | None:
    """The caller's account id, or None for an anonymous visitor.

    Only the backend can reach the agent. It sends signed-in visitors with their
    account id and anonymous ones as "anon:<hash of a browser-chosen id>" —
    which anyone can mint fresh, so it must not unlock paid work.
    """
    user_id = ((config or {}).get("configurable") or {}).get("user_id") or ""
    if not user_id or user_id.startswith("anon:"):
        return None
    return user_id
