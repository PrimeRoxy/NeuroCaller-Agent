from pydantic_settings import BaseSettings
from typing import Optional, Dict, Any
from functools import lru_cache
from dotenv import load_dotenv
from pathlib import Path
import os, json
import redis

# Get absolute path to the .env file in the project root
current_file_path = Path(__file__).resolve()
project_root = current_file_path.parent.parent
env_path = project_root / ".env"

load_dotenv(dotenv_path=env_path, override=True) 

REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB: int = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD: Optional[str] = os.getenv("REDIS_PASSWORD")  # Optional
REDIS_TIMEOUT: int = (os.getenv("REDIS_TIMEOUT", 10))  # Optional: connection timeout

class Settings(BaseSettings):
    PROJECT_NAME: str = "SalesAgent"
    RATE_LIMIT_PER_MINUTE: int = 60
    MONGO_URI: str = "mongodb://localhost:27017/calls"
    LOG_DB_NAME: Optional[str] = None
    LOG_COLLECTION_NAME: Optional[str] = None
    CALL_SERVICE_URL: Optional[str] = None
    PROMPT_TEMPLATE: Optional[str] = None
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_USERNAME: str =""
    REDIS_PASSWORD: str =""

    OPENAI_API_KEY: Optional[str] = None
    QDRANT_URL: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None
    PLIVO_AUTH_ID: Optional[str] = None
    PLIVO_AUTH_TOKEN: Optional[str] = None
    PLIVO_NUMBER: Optional[str] = None
    PLIVO_ANSWER_URL: Optional[str] = None
    PLIVO_HANGUP_URL: Optional[str] = None
    PLIVO_ACTION_URL: Optional[str] = None
    AWS_S3_BUCKET: Optional[str] = None
    S3_REGION: Optional[str] = None
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    WSS_URL: Optional[str] = None
    DB_URL: Optional[str] = None
    SCHEDULER_URL: Optional[str] = None
    
    
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_PHONE_NUMBER: Optional[str] = None
    SERVER_BASE_URL: Optional[str] = None
    
    VOICELINK_USERNAME: Optional[str] = None
    VOICELINK_PASSWORD: Optional[str] = None
    VOICELINK_TOKEN: Optional[str] = None
    VOICELINK_LOGIN_URL: Optional[str] = None
    VOICELINK_NUMBER: Optional[str] = None
    VOICELINK_PRODUCTION_NUMBERS: Optional[str] = None
    VOICELINK_LIST_ID: Optional[str] = None
    VOICELINK_DID_URL: Optional[str] = "https://greeter.co.in/api/get-did-list"
    VOICELINK_DID_TOKEN: Optional[str] = None
    
    SARVAM_API_KEY: Optional[str] = None
    SARVAM_STT_MODEL: Optional[str] = None
    SARVAM_STT_MODE: Optional[str] = None
    SARVAM_STT_LANGUAGE: Optional[str] = None
    SARVAM_TTS_LANGUAGE: Optional[str] = None
    SARVAM_STT_SAMPLE_RATE: Optional[int] = None
    SARVAM_STT_HIGH_VAD_SENSITIVITY: Optional[bool] = None
    SARVAM_STT_VAD_SIGNALS: Optional[bool] = None
    SARVAM_STT_ENABLE_FLUSH_SIGNAL: Optional[bool] = None
    SARVAM_TTS_MODEL: Optional[str] = None
    SARVAM_TTS_SPEAKER: Optional[str] = None
    SARVAM_TTS_PACE: Optional[float] = None
    SARVAM_TTS_TEMPERATURE: Optional[float] = None
    SARVAM_TTS_MIN_BUFFER_SIZE: Optional[int] = None
    SARVAM_TTS_MAX_CHUNK_LENGTH: Optional[int] = None
    
    SILERO_FORWARD_THRESHOLD: Optional[float] = None
    SILERO_BARGEIN_THRESHOLD: Optional[float] = None
    BARGEIN_STRONG_THRESHOLD: Optional[float] = None
    BARGEIN_RESET_THRESHOLD: Optional[float] = None
    BARGEIN_MIN_AVG_PROB: Optional[float] = None
    
    ENABLE_FILLER_AUDIO: Optional[bool] = None
    OPENAI_MODEL: Optional[str] = None
    SARVAM_LLM_MODEL: Optional[str] = None
    GEMINI_FLASH_MODEL: Optional[str] = "gemini-flash-latest"
    GEMINI_FLASH_LITE_MODEL: Optional[str] = "gemini-flash-lite-latest"
    
    PRODUCTION_URL: str = "https://adksalescalling.allyra.ai"
    
    VOICELINK_HANGUP_URL: Optional[str] = None
    
    FREJUN_API_KEY: Optional[str] = None
    FREJUN_FROM_NUMBER: Optional[str] = None
    FREJUN_API_BASE_URL: Optional[str] = None

    class Config:
        case_sensitive = True
        env_file = str(env_path)
        extra="allow"

def is_production() -> bool:
    """Check if active environment is production based on PRODUCTION_URL or ENVIRONMENT variable."""
    server_url = (os.getenv("SERVER_BASE_URL") or "").rstrip("/")
    prod_url = (os.getenv("PRODUCTION_URL") or "https://adksalescalling.allyra.ai").rstrip("/")
    return bool(server_url and server_url == prod_url) 




@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
redis_client = redis.Redis(host=settings.REDIS_HOST,
                           port=settings.REDIS_PORT,
                           db=settings.REDIS_DB,
                           username=settings.REDIS_USERNAME,
                           password=settings.REDIS_PASSWORD,
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

# Test Redis connection
def test_redis_connection():
    """
    Test Redis connection
    """
    try:
        response = redis_client.ping()
        print(f"Redis connection successful: {response}")
        return True
    except Exception as e:
        print(f"Redis connection failed: {e}")
        return False