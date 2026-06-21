"""Start the RQ worker — avoids Windows script-path issues with spaces."""
import redis
from rq import Worker, Queue
from app.config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD

conn = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD or None,
    decode_responses=False,
)
queue = Queue("default", connection=conn)
worker = Worker([queue], connection=conn)
print(f"Worker started — listening on 'default' queue at {REDIS_HOST}:{REDIS_PORT}")
worker.work()
