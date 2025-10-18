from pydantic_settings import BaseSettings
from typing import Optional, Dict, Any
from functools import lru_cache
from dotenv import load_dotenv
import os, json
import redis

load_dotenv(override=True) 

REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB: int = int(os.getenv("REDIS_DB", 0))

class Settings(BaseSettings):
    PROJECT_NAME: str = "SalesAgent"
    RATE_LIMIT_PER_MINUTE: int = 60
    MONGODB_URI: str = "mongodb://localhost:27017/"
    LOG_DB_NAME: Optional[str] = None
    LOG_COLLECTION_NAME: Optional[str] = None
    CALL_SERVICE_URL: Optional[str] = None
    QDRANT_COLLECTION_NAME: str = "salesagent_collection"
    QDRANT_VECTOR_SIZE: int = 1536
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    PROMPT_TEMPLATE: Optional[str] = None
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    class Config:
        case_sensitive = True
        env_file = ".env"
        extra="allow"

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
redis_client = redis.Redis(host=settings.REDIS_HOST,
                           port=settings.REDIS_PORT,
                           db=settings.REDIS_DB,
                           decode_responses=True,
                           )

# Helper function to set JSON data in Redis
def set_redis_json(key: str, data: dict, expire: Optional[int] = 86400):
    """
    Set JSON data in Redis with optional expiration
    """
    try:
        redis_client.set(key, json.dumps(data), ex=expire)
        return True
    except Exception as e:
        print(f"Error setting Redis key {key}: {e}")
        return False

# Helper function to get JSON data from Redis
def get_redis_json(key: str) -> Optional[dict]:
    """
    Get JSON data from Redis
    """
    try:
        data = redis_client.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        print(f"Error getting Redis key {key}: {e}")
        return None