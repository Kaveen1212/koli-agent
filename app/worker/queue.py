import redis
from rq import Queue
from app.config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD


def get_queue() -> Queue:
    """Connect to Redis and return the default RQ Queue."""
    connection = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD or None,
        decode_responses=False,
    )
    return Queue("default", connection=connection)
