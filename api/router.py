from services.prompt_session_service import PromptSessionService
from services.config_service import AVAILABLE_VOICES
from services.voice_mapping import INDIAN_TO_GEMINI_VOICE
from io import BytesIO
import json
import logging
import os
import re
import uuid
import PyPDF2
from bson import ObjectId
import docx
from fastapi import BackgroundTasks, File, Form, HTTPException, Query, UploadFile, Request
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime
from fastapi import APIRouter
import boto3
from dotenv import load_dotenv
from services.uploader import upload_file_for_gemini,convert_docx_to_pdf
from utils.plivo_number import get_available_countries, get_rented_numbers
load_dotenv()
from services.call_service_voicelink import orgcalls_collection,calls_collection, mongo_client
from agent.salesmate_agent import SalesmateAgent
from core.config import redis_client
import time
router = APIRouter()
from services.config_service import ConfigService

# Initialize agent ONCE outside the function to keep session service alive
agent = SalesmateAgent() 

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("S3_REGION")
S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET")

s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)


class FileResource(BaseModel):
    file: str
    uploaded_at: datetime

class Voice(BaseModel):
    name: str
    gender: str
    audio_url: str
    voice_id: Optional[str] = None

class AllyroidConfig(BaseModel):
    organisation_id: str
    organisation_name: str
    welcome_message: str
    country_code: str
    phone_number: str
    timezone: Optional[str] = None
    uploaded_resources: List[FileResource] = []

# ---------- Robust JSON cleaner ----------
_JSON_LINE_COMMENT = re.compile(r"//.*?$", re.MULTILINE)
_JSON_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_JSON_TRAILING_COMMA = re.compile(r",(?=\s*[}\]])")
_JSON_SINGLE_QUOTED_KEY = re.compile(r'([{\s,])\'([^\'"]+?)\'\s*:')
_JSON_SINGLE_QUOTED_VAL = re.compile(r':\s*\'([^\'"\\]*?)\'')

def _clean_json_like(s: str) -> str:
    """Make common JSON5-ish files closer to strict JSON."""
    s = _JSON_LINE_COMMENT.sub("", s)
    s = _JSON_BLOCK_COMMENT.sub("", s)
    s = _JSON_TRAILING_COMMA.sub("", s)
    s = _JSON_SINGLE_QUOTED_KEY.sub(r'\1"\2":', s)
    s = _JSON_SINGLE_QUOTED_VAL.sub(r': "\1"', s)
    return s



@router.post("/api/allyroid/config")
async def upsert_config(
    user_id: str = Form(...),
    organisation_id: str = Form(...),
    allyroid_name: Optional[str] = Form(None),
    organisation_name: Optional[str] = Form(None),
    allyroid_voice: Optional[str] = Form(None),
    allyroid_gender: Optional[str] = Form(None),
    phone_numbers: Optional[List[str]] = Form(None),
    crm_provider: Optional[str] = Form(None),
    script: Optional[str] = Form(None),
    timezone: Optional[str] = Form(None),
    working_hours_start: Optional[str] = Form(None),
    working_hours_end: Optional[str] = Form(None),
    quiet_hours_start: Optional[str] = Form(None),
    quiet_hours_end: Optional[str] = Form(None),
    # Salesforce Config Inputs
    salesforce_client_id: Optional[str] = Form(None),
    salesforce_client_secret: Optional[str] = Form(None),
    salesforce_domain: Optional[str] = Form(None)
):
    """
    Create or update Allyroid configuration.
    Supports multiple phone numbers, voice selection, CRM provider, script, and timezone.
    Also supports Salesforce credentials for CRM integration.
    """
    try:
        return await ConfigService.upsert_config(
            user_id=user_id,
            organisation_id=organisation_id,
            allyroid_name=allyroid_name,
            organisation_name=organisation_name,
            allyroid_voice=allyroid_voice,
            allyroid_gender=allyroid_gender,
            phone_numbers=phone_numbers,
            crm_provider=crm_provider,
            script=script,
            timezone=timezone,
            working_hours_start=working_hours_start,
            working_hours_end=working_hours_end,
            quiet_hours_start=quiet_hours_start,
            quiet_hours_end=quiet_hours_end,
            # Salesforce
            salesforce_client_id=salesforce_client_id,
            salesforce_client_secret=salesforce_client_secret,
            salesforce_domain=salesforce_domain,
        )
    except Exception as e:
        logging.error(f"Error in upsert_config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/allyroid/config/{organisation_id}")
async def get_config(
    organisation_id: str,
    user_id: str = Query(...)
):
    """
    Fetch Allyroid configuration for a given organisation_id.
    """
    return ConfigService.get_config(organisation_id=organisation_id, user_id=user_id)
    

@router.delete("/api/allyroid/config/{organisation_id}")
async def delete_config(
    organisation_id: str,
    user_id: str = Query(...)
):
    """
    Delete configuration for a given organisation_id.
    """
    return ConfigService.delete_config(user_id, organisation_id)


def convert_objectid(data):
    if isinstance(data, list):
        return [convert_objectid(item) for item in data]
    elif isinstance(data, dict):
        return {key: convert_objectid(value) for key, value in data.items()}
    elif isinstance(data, ObjectId):
        return str(data)
    else:
        return data
@router.post("/api/allyroid/config/{organisation_id}")
async def update_config(
    organisation_id: str,
    user_id: str = Form(...),
    allyroid_name: Optional[str] = Form(None),
    organisation_name: Optional[str] = Form(None),
    allyroid_voice: Optional[str] = Form(None),
    allyroid_gender: Optional[str] = Form(None),
    phone_numbers: Optional[List[str]] = Form(None),
    crm_provider: Optional[str] = Form(None),
    script: Optional[str] = Form(None),
    timezone: Optional[str] = Form(None),
    working_hours_start: Optional[str] = Form(None),
    working_hours_end: Optional[str] = Form(None),
    quiet_hours_start: Optional[str] = Form(None),
    quiet_hours_end: Optional[str] = Form(None),
    # Salesforce Config Inputs
    salesforce_client_id: Optional[str] = Form(None),
    salesforce_client_secret: Optional[str] = Form(None),
    salesforce_domain: Optional[str] = Form(None)
):
    """
    Update endpoint for Allyroid configuration.
    Delegates to the same upsert logic.
    """
    return await ConfigService.upsert_config(
        user_id=user_id,
        organisation_id=organisation_id,
        allyroid_name=allyroid_name,
        organisation_name=organisation_name,
        allyroid_voice=allyroid_voice,
        allyroid_gender=allyroid_gender,
        phone_numbers=phone_numbers,
        crm_provider=crm_provider,
        script=script,
        timezone=timezone,
        working_hours_start=working_hours_start,
        working_hours_end=working_hours_end,
        quiet_hours_start=quiet_hours_start,
        quiet_hours_end=quiet_hours_end,
        # Salesforce
        salesforce_client_id=salesforce_client_id,
        salesforce_client_secret=salesforce_client_secret,
        salesforce_domain=salesforce_domain
    )

@router.get("/countries")
def list_countries():
    """
    Endpoint to list countries where Plivo offers phone numbers.
    """
    countries = get_available_countries()
    return {"available_countries": countries}

@router.get("/rented-numbers")
def list_rented_numbers(country_code: Optional[str] = Query(None, description="ISO country code to filter numbers")):
    """
    Endpoint to list rented phone numbers, optionally filtered by country code.
    """
    try:
        numbers = get_rented_numbers(country_code)
        return {"rented_numbers": numbers}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/calls/by-user-org")
def get_calls_by_user_org(
    organisation_id: str = Query(..., description="Organisation ID"),
    user_id: Optional[str] = Query(None, description="User ID (optional)"),
):
    """
    Fetch call records from MongoDB for a specific organisation_id.
    If `user_id` is provided, filter by both organisation_id and user_id.
    """
    try:
        # Build the MongoDB filter (only include user_id when provided)
        filt = {"organisation_id": organisation_id}
        if user_id:
            filt["user_id"] = user_id

        # Query MongoDB (exclude _id for cleaner response)
        records = list(calls_collection.find(filt, {"_id": 0}))
        count = len(records)

        if count == 0:
            return {
                "status": "success",
                "message": "No call records found."
                           + (" for this user and organisation." if user_id else " for this organisation."),
                "organisation_id": organisation_id,
                "user_id": user_id,
                "count": 0,
                "calls": []
            }

        return {
            "status": "success",
            "organisation_id": organisation_id,
            "user_id": user_id,
            "count": count,
            "calls": records
        }

    except Exception as e:
        logging.error(f"Error fetching call data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching call data: {str(e)}")

# ============================================================================
# SCHEDULER MANAGEMENT API
# ============================================================================

from services.scheduler_service import get_scheduled_tasks, delete_scheduled_task

@router.get("/api/scheduler/tasks")
def list_scheduler_tasks():
    """
    List all pending scheduled call tasks.
    """
    try:
        tasks = get_scheduled_tasks()
        return {"count": len(tasks), "tasks": tasks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/scheduler/tasks")
def remove_scheduler_task(
    campaign_id: str = Query(..., description="Campaign ID of the task"),
    to_number: str = Query(..., description="Phone number associated with the task")
):
    """
    Manually remove a scheduled task.
    """
    try:
        count = delete_scheduled_task(campaign_id, to_number)
        if count > 0:
            return {"status": "success", "message": f"Removed task for {to_number} in campaign {campaign_id}"}
        else:
            return {"status": "not_found", "message": "Task not found."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/salesmate", tags=["NeuroCaller Agent"])
async def search_multimodal(
    background_tasks: BackgroundTasks,
    query: str = Form(...),
    session_id: str = Form(None),
    user_id: str = Form("default_user"),
    org_id: str = Form(None),  # Optional org_id for campaign tracking
    files: Optional[List[UploadFile]] = File(default=None)
):
    """
    Chat with the NeuroCaller agent.
    
    The agent handles everything conversationally:
    - Understanding your intent (sales, follow-up, cold call, etc.)
    - Extracting phone numbers from text or uploaded files
    - Processing sales scripts and FAQs from chat or files
    - Initiating outbound calls when ready
    
    Just chat naturally! Examples:
    - "I want to call these leads: +919876543210, +919123456789"
    - "Start a sales campaign for product X" (then upload a file with numbers)
    - "Here's my sales script: [paste text]"
    """
    logging.info("="*50)
    logging.info("[/salesmate] REQUEST RECEIVED")
    logging.info(f"[/salesmate] User: {user_id}, Org: {org_id}")
    
    uploaded_part = None
    temp_file_to_cleanup = None

    # File handling (single file support for now)
    if files:
        for file in files:
            if not file or not file.filename:
                continue

            filename = file.filename.lower()
            content_type = file.content_type

            logging.info(f"Processing file: {filename} ({content_type})")

            if filename.endswith(".docx"):
                logging.info("Converting DOCX to PDF before upload...")
                pdf_file = convert_docx_to_pdf(file.file)
                if not pdf_file:
                    return {"error": "Failed to convert DOCX to PDF."}
                uploaded_part = upload_file_for_gemini(pdf_file, "application/pdf")
                temp_file_to_cleanup = pdf_file

            elif filename.endswith(".doc"):
                return {"error": "Legacy .doc files not supported. Please use .docx or .pdf."}

            else:
                try:
                    file.file.seek(0)
                except Exception:
                    pass
                uploaded_part = upload_file_for_gemini(file.file, content_type)

            if not uploaded_part:
                return {"error": f"File upload failed: {file.filename}"}

            logging.info(f"File uploaded successfully: {file.filename}")
            break  # Limit: Gemini supports only one file at a time
    
    # Run the agent
    logging.info("[/salesmate] Calling agent.run()...")
    start_time = time.time()
    result = await agent.run(
        query=query, 
        session_id=session_id, 
        user_id=user_id, 
        org_id=org_id,
        uploaded_file=uploaded_part
    )
    end_time = time.time()
    time_taken = end_time - start_time
    return {
        "message": result.get("message") or result.get("error"),
        "session_id": result.get("session_id"),
        "time_taken": round(time_taken, 2),
        "token_details": result.get("token_details", {})
    }

@router.get("/health", tags=["Health Check"])
async def router_health_check():
    """
    Health check endpoint to verify service connectivity.
    Checks:
    - Redis
    - MongoDB
    """
    health_status = {
        "status": "healthy",
        "services": {
            "redis": "unknown",
            "mongodb": "unknown",
            "postgres": "not_configured" 
        },
        "timestamp": datetime.utcnow()
    }
    
    # Redis Check
    try:
        if redis_client.ping():
             health_status["services"]["redis"] = "connected"
    except Exception as e:
         health_status["services"]["redis"] = f"error: {str(e)}"
         health_status["status"] = "unhealthy"

    # MongoDB Check
    try:
        # Simple command to check connection
        mongo_client.admin.command('ping')
        health_status["services"]["mongodb"] = "connected"
    except Exception as e:
        health_status["services"]["mongodb"] = f"error: {str(e)}"
        health_status["status"] = "unhealthy"

    if health_status["status"] == "unhealthy":
        raise HTTPException(status_code=503, detail=health_status)
        
    return health_status

VOICE_GENDERS = {
    "alloy": "male",
    "ash": "male",
    "ballad": "male",
    "cedar": "male",
    "coral": "female",
    "echo": "male",
    "marin": "female",
    "sage": "female",
    "shimmer": "female",
    "verse": "male",
    # India-specific Gemini Live voices
    "priya": "female",
    "ananya": "female",
    "kavya": "female",
    "meera": "female",
    "nisha": "female",
    "diya": "female",
    "sneha": "female",
    "pooja": "female",
    "riya": "female",
    "tara": "female",
    "neha": "female",
    "simran": "female",
    "anjali": "female",
    "swati": "female",
    "divya": "female",
    "arjun": "male",
    "vikram": "male",
    "rohan": "male",
    "aditya": "male",
    "ishaan": "male",
    "ravi": "male",
    "karan": "male",
    "deepak": "male",
    "suresh": "male",
    "aarav": "male",
    "rajesh": "male",
    "manoj": "male",
    "sanjay": "male",
    "amit": "male",
    "rahul": "male",
}

GEMINI_LIVE_VOICES = {
    # Female voices (11)
    "ananya": "female",
    "anjali": "female",
    "divya": "female",
    "diya": "female",
    "kavya": "female",
    "meera": "female",
    "nisha": "female",
    "pooja": "female",
    "priya": "female",
    "riya": "female",
    "tara": "female",
    # Male voices (13)
    "aditya": "male",
    "amit": "male",
    "arjun": "male",
    "deepak": "male",
    "ishaan": "male",
    "karan": "male",
    "rahul": "male",
    "rajesh": "male",
    "ravi": "male",
    "rohan": "male",
    "sanjay": "male",
    "suresh": "male",
    "vikram": "male",
}

INDIAN_GEMINI_VOICES = GEMINI_LIVE_VOICES

GEMINI_LIVE_DIR = os.path.join("audio_samples", "audio_samples_gemini_live")

def _build_voice_catalogs():
    indian_voices = []
    if os.path.exists(GEMINI_LIVE_DIR):
        for filename in sorted(os.listdir(GEMINI_LIVE_DIR)):
            if filename.endswith(".wav"):
                v_name = filename[:-4].lower()
                v_gender = GEMINI_LIVE_VOICES.get(v_name, VOICE_GENDERS.get(v_name, "neutral"))
                indian_voices.append({
                    "name": v_name,
                    "gender": v_gender,
                    "rel_path": f"audio_samples/audio_samples_gemini_live/{filename}",
                    "voice_id": INDIAN_TO_GEMINI_VOICE.get(v_name),
                })

    default_voices = []
    audio_samples_dir = "audio_samples"
    if os.path.exists(audio_samples_dir):
        for filename in sorted(os.listdir(audio_samples_dir)):
            if filename.endswith(".mp3"):
                v_name = filename[:-4].lower()
                if v_name in AVAILABLE_VOICES:
                    v_gender = VOICE_GENDERS.get(v_name, "neutral")
                    default_voices.append({
                        "name": v_name,
                        "gender": v_gender,
                        "rel_path": f"audio_samples/{filename}",
                        "voice_id": v_name,
                    })

    return indian_voices, default_voices

CACHED_INDIAN_VOICES, CACHED_DEFAULT_VOICES = _build_voice_catalogs()


@router.get("/voices", response_model=List[Voice])
def list_voices(
    request: Request,
    gender: Optional[str] = Query(None, description="Filter voices by gender (male/female)"),
    country: Optional[str] = Query(None, description="Filter voices by country (e.g. India, UAE, US, IN)"),
    country_code: Optional[str] = Query(None, description="Filter voices by country code (e.g. IN, UAE, US)")
):
    """
    List available voices with their details and sample audio URLs.
    Only India ('IN', 'India') returns Indian Gemini Live voices.
    All other countries and default (e.g., UAE, US) return default OpenAI voices.
    """
    server_base = os.getenv("SERVER_BASE_URL", "").strip().rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"

    if "localhost" in host or "127.0.0.1" in host:
        base_url = f"http://{host}".rstrip("/")
    elif server_base and ("ngrok" in server_base or "https" in server_base):
        base_url = server_base
    elif host:
        base_url = f"{proto}://{host}".rstrip("/")
    else:
        base_url = str(request.base_url).rstrip("/")

    norm_country = (country or country_code or "").strip().lower()
    is_india = norm_country in ("in", "india", "ind")

    catalog = CACHED_INDIAN_VOICES if is_india else CACHED_DEFAULT_VOICES
    norm_gender = gender.strip().lower() if gender else None

    return [
        Voice(
            name=item["name"],
            gender=item["gender"],
            audio_url=f"{base_url}/{item['rel_path']}",
            voice_id=item["voice_id"]
        )
        for item in catalog
        if not norm_gender or item["gender"].lower() == norm_gender
    ]

@router.get("/api/prompt/check", tags=["Prompt Check"])
async def check_prompt(
    user_id: str = Query(..., description="User ID"),
    org_id: str = Query(..., description="Organization ID"),
    password: str = Query(..., description="Access password"),
    prompt_name: Optional[str] = Query(None, description="Optional Prompt Name")
):
    """
    Check prompt generated based on userid org id and optional prompt name.
    """
    if password != "sales12":
        raise HTTPException(status_code=401, detail="Unauthorized access.")

    session_service = PromptSessionService()

    try:
        if prompt_name:
            prompt_data = session_service.get_prompt_by_name(org_id=org_id, user_id=user_id, prompt_name=prompt_name)
        else:
            prompt_data = session_service.get_latest_prompt(org_id=org_id, user_id=user_id)

        if not prompt_data:
            raise HTTPException(status_code=404, detail="Prompt not found.")

        return {
            "status": "success",
            "prompt": prompt_data
        }
    except Exception as e:
        logging.error(f"Error fetching prompt: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
