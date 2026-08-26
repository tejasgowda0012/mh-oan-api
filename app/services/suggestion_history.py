import redis.asyncio as redis
import os
import json
from typing import Set

# Read from env (similar to config.py but isolated for simplicity)
REDIS_HOST = os.getenv("REDIS_HOST", "redis-stack")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

_redis_client = None

def get_redis_client():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)
    return _redis_client

def get_key(session_id: str) -> str:
    return f"sva-cache-suggestions_blacklist:{session_id}"

async def get_past_suggestions(session_id: str) -> Set[str]:
    """Get all past user questions and suggestion chips for this session."""
    client = get_redis_client()
    key = get_key(session_id)
    members = await client.smembers(key)
    return set(members) if members else set()

async def add_to_blacklist(session_id: str, text: str) -> None:
    """Add a question/chip to the session blacklist with a 24h TTL."""
    if not text or not text.strip():
        return
    client = get_redis_client()
    key = get_key(session_id)
    # Add to set
    await client.sadd(key, text.strip())
    # Ensure 24h TTL is set (if not already)
    await client.expire(key, 86400)
