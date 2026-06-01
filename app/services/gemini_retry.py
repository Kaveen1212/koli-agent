"""Shared retry wrapper for Gemini calls — handles transient 503/429 overload errors."""
import time
from google.genai import errors


def with_retry(fn, *args, max_attempts: int = 4, base_delay: float = 2.0, **kwargs):
    """Call fn(*args, **kwargs), retrying on transient Gemini errors (503/429).
    Uses exponential backoff: 2s, 4s, 8s. Re-raises after max_attempts.
    """
    last_error = None
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except (errors.ServerError, errors.ClientError) as e:
            code = getattr(e, "code", None)
            if code in (429, 503):  # rate limit or overload — retry
                last_error = e
                if attempt < max_attempts - 1:
                    time.sleep(base_delay * (2 ** attempt))
                    continue
            raise
    raise last_error
