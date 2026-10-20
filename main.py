import sys
import os
import asyncio
import logging
import sys

# Auto-inject virtual environment site-packages into sys.path to prevent global package conflicts
try:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    venv_dir = os.path.join(base_dir, ".venv")
    if os.path.exists(venv_dir):
        lib_dir = os.path.join(venv_dir, "lib")
        if os.path.exists(lib_dir):
            for py_dir in os.listdir(lib_dir):
                sp_dir = os.path.join(lib_dir, py_dir, "site-packages")
                if os.path.exists(sp_dir) and sp_dir not in sys.path:
                    sys.path.insert(0, sp_dir)
                    break
except Exception:
    pass

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from api.router import router
from api.salesforce_router import router as salesforce_router
from api.dashboard_endpoints import router as dashboard_router
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from services import call_service_plivo, call_plivo, call_service_voicelink, call_gemini_live
from services.scheduler_service import run_scheduler_loop
from services.quiet_hours_watcher import run_quiet_hours_watcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="NeuroCaller API",
    description="Sales Calling Agent with Campaign Tracking and Dashboard",
    version="2.0.0"
)
# Load .env file using absolute path before importing other modules
try:
    from dotenv import load_dotenv
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(base_dir, ".env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=True)
        print(f"[DotEnv Loader] Loaded environment variables from: {env_path}", file=sys.stderr)
    else:
        print(f"[DotEnv Loader] Warning: .env file not found at {env_path}", file=sys.stderr)
except Exception as e:
    print(f"[DotEnv Loader] Error loading .env: {e}", file=sys.stderr)

@app.on_event("startup")
async def startup_event():
    # Log loaded environment variables (obfuscating secrets)
    try:
        from core.config import settings
        logger.info("=== Loaded Environment Variables (obfuscated) ===")
        sensitive_keywords = ["key", "secret", "token", "password", "auth", "id", "uri", "url"]
        # Use settings.dict() or settings.model_dump() depending on Pydantic version
        settings_dict = {}
        if hasattr(settings, "model_dump"):
            settings_dict = settings.model_dump()
        else:
            settings_dict = settings.dict()
            
        for key, val in settings_dict.items():
            if val is None:
                logger.info(f"{key}: None")
                continue
            is_sensitive = any(kw in key.lower() for kw in sensitive_keywords)
            if is_sensitive:
                s_val = str(val).strip()
                if len(s_val) <= 8:
                    obfuscated_val = "****"
                else:
                    obfuscated_val = f"{s_val[:4]}...{s_val[-4:]}"
                logger.info(f"{key}: {obfuscated_val}")
            else:
                logger.info(f"{key}: {val}")
        logger.info("================================================")
    except Exception as e:
        logger.error(f"Failed to log environment variables: {e}")

    asyncio.create_task(run_scheduler_loop())
    asyncio.create_task(run_quiet_hours_watcher())

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(salesforce_router)  # Add Salesforce Router
app.include_router(call_service_plivo.router)         # Plivo REST endpoints
app.include_router(call_plivo.router)                 # Plivo WebSocket
app.include_router(call_service_voicelink.router)     # VoiceLink REST endpoints
app.include_router(call_gemini_live.router)          # VoiceLink Gemini Live WebSocket
app.include_router(dashboard_router)
from live_monitor.live_call_socket import router as live_call_socket_router
app.include_router(live_call_socket_router)          # Live call updates WebSocket
from services.quick_call_api import router as quick_call_router
app.include_router(quick_call_router)                 # Quick one-number call API

# Mount audio samples
app.mount("/audio_samples", StaticFiles(directory="audio_samples"), name="audio_samples")

# ── NeuroCaller frontend (landing page + platform app), served from utils/ ──
# Only utils/assets is exposed; the Python modules in utils/ stay private.
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "utils")
app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIR, "assets")), name="frontend_assets")


@app.get("/", include_in_schema=False)
async def landing_page():
    """Public NeuroCaller landing page."""
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/app", include_in_schema=False)
async def platform_app():
    """NeuroCaller platform: dashboard, live monitor, campaigns, agent studio, copilot."""
    return FileResponse(os.path.join(FRONTEND_DIR, "app.html"), headers={"Cache-Control": "no-cache"})


@app.get("/dashboard", include_in_schema=False)
async def legacy_dashboard():
    """Old dashboard URL; the platform app replaces it."""
    return RedirectResponse(url="/app", status_code=308)


@app.get("/health")
async def health_check():
    """
    200: process up; Mongo + Redis (and optional PostgreSQL if DB_URL) healthy.
    500: dependency failed — detail indicates class of failure.
    """
    from pymongo import MongoClient

    from core.config import get_settings, redis_client

    settings = get_settings()

    try:
        def _mongo_ping():
            client = MongoClient(
                settings.MONGO_URI, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000
            )
            try:
                client.admin.command("ping")
            finally:
                client.close()

        await asyncio.to_thread(_mongo_ping)
    except Exception as e:
        logger.error("Health: MongoDB ping failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Database connection failed"
        ) from e

    try:
        await asyncio.to_thread(redis_client.ping)
    except Exception as e:
        logger.error("Health: Redis ping failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500, detail="Redis connection failed"
        ) from e

    db_url = (os.getenv("DB_URL") or "").strip()
    if db_url:
        try:
            import asyncpg

            conn = await asyncio.wait_for(asyncpg.connect(db_url), timeout=5.0)
            try:
                await conn.execute("SELECT 1")
            finally:
                await conn.close()
        except Exception as e:
            logger.error("Health: PostgreSQL ping failed: %s", e, exc_info=True)
            raise HTTPException(
                status_code=500, detail="Database connection failed"
            ) from e

    body = {
        "status": "healthy",
        "server": "ok",
        "database": "ok",
        "redis": "ok",
        "service": "NeuroCaller API",
    }
    if db_url:
        body["postgres_cache"] = "ok"
    return body