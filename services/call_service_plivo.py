import asyncio
from datetime import datetime
import json
from fastapi import Request, Response,APIRouter
from plivo import RestClient
import os
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
from plivo import plivoxml
import logging
from services.uploader import upload_url_to_s3
from fastapi import BackgroundTasks
from pymongo import MongoClient
from core.config import redis_client
from services.prompt_session_service import PromptSessionService
from services.analysis_service import analyze_call_outcome
from services.campaign_tracking_service import get_campaign_tracking_service
from services.task_event_service import get_task_event_service

# Import tracking and event services (lazy to avoid circular imports)
def get_tracking_service():
    return get_campaign_tracking_service()

def get_event_service():
    return get_task_event_service()

load_dotenv()

PLIVO_AUTH_ID = os.getenv("PLIVO_AUTH_ID")
PLIVO_AUTH_TOKEN = os.getenv("PLIVO_AUTH_TOKEN")
PLIVO_NUMBER = os.getenv("PLIVO_NUMBER")
PLIVO_ANSWER_URL = os.getenv("PLIVO_ANSWER_URL")
PLIVO_ACTION_URL = os.getenv("PLIVO_ACTION_URL")
PLIVO_HANGUP_URL = os.getenv("PLIVO_HANGUP_URL")
PLIVO_TRANSCRIPT_URL = f"{PLIVO_ACTION_URL}/transcription"
# PLIVO_WSS_URL = os.getenv("PLIVO_WSS_URL")

class CallRequest(BaseModel):
    phone_number: str
    lead_name: Optional[str] = ""
    welcome_message: Optional[str] = None
    prompt_name: Optional[str] = None
    organisation_id: Optional[str] = "manual"
    user_id: Optional[str] = "api_user"

router = APIRouter()
client = RestClient(auth_id=PLIVO_AUTH_ID, auth_token=PLIVO_AUTH_TOKEN)


# MongoDB connection
mongo_client = MongoClient(os.getenv("MONGO_URI"))
db = mongo_client.get_database()
calls_collection = db["calls"]
orgcalls_collection = db["orgcalls"]

def get_campaign_id(organisation_id: str | None, user_id: str | None) -> str:
    """
    Build a stable-enough campaign id even if org/user are missing.
    Example outputs:
      org123:user456:20251011T134500Z
      public:nouser:20251011T134500Z
    """
    org = organisation_id or "public"
    user = user_id or "nouser"
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return f"{org}:{user}:{ts}"

def q_keys(campaign_id: str):
    return {
        "queue": f"call_queue:{campaign_id}",
        "config": f"call_queue_config:{campaign_id}",
    }

def outbound_call(
    leads: list[dict] | str, 
    organisation_id: str | None = None, 
    user_id: str | None = None, 
    plivo_number: str | None = None, 
    prompt_name: str | None = None,
    welcome_message: str | None = None,
    # agent_name: str | None = None,
    # company_name: str | None = None,
):
    """
    Store numbers in Redis queue and start first call.
    Also creates campaign tracking record and emits events.
    
    Args:
        leads: List of dicts with {"number": "...", "name": "..."} OR comma-separated string (compat)
        organisation_id: Organization ID for tracking
        user_id: User ID for tracking
        plivo_number: The Plivo number to call FROM (extracted from chat).
        prompt_name: Optional name of the saved prompt to use. If None, uses latest.
        welcome_message: Optional custom welcome message/opening line.
    """
    logging.info(f"[outbound_call] START")
    
    # Normalize input to list of dicts
    final_leads = []
    if isinstance(leads, str):
        # Backward compatibility
        nums = [n.strip() for n in leads.split(",") if n.strip()]
        for n in nums:
            final_leads.append({"number": n, "name": ""})
            logging.info(f"[outbound_call] Processing numbers string input: {len(final_leads)} leads")
    else:
        final_leads = leads
        logging.info(f"[outbound_call] Processing list input: {len(final_leads)} leads")

    logging.info(f"[outbound_call] Plivo number (from chat): {plivo_number}")
    logging.info(f"[outbound_call] Org: {organisation_id}, User: {user_id}")
    logging.info(f"[outbound_call] Prompt Name: {prompt_name}")
    logging.info(f"[outbound_call] Welcome Message: {welcome_message}")
    
    if not final_leads:
        logging.warning("[outbound_call] No leads provided")
        return {"status": "failed", "msg": "No leads provided"}
    
    # Initialize variables
    full_phone_number = plivo_number
    # welcome_message is passed as argument
    system_prompt = None 
    
    # Resolve Prompt using PromptSessionService
    try:
        session_service = PromptSessionService()
        
        # Determine which prompt to use
        stored_prompt_data = None
        if prompt_name:
            stored_prompt_data = session_service.get_prompt_by_name(
                org_id=organisation_id or "public",
                user_id=user_id or "nouser",
                prompt_name=prompt_name
            )
            if not stored_prompt_data:
                logging.warning(f"[outbound_call] Prompt '{prompt_name}' not found. Falling back to latest.")
        
        # If no name provided or named prompt not found, fetch latest
        if not stored_prompt_data:
            stored_prompt_data = session_service.get_latest_prompt(
                org_id=organisation_id or "public",
                user_id=user_id or "nouser"
            )
            
        if stored_prompt_data:
            system_prompt = stored_prompt_data.get("prompt_text")
            logging.info(f"[outbound_call] Using prompt '{stored_prompt_data.get('prompt_name')}'")
        else:
             logging.warning("[outbound_call] No saved prompt found for this user.")

    except Exception as e:
        logging.error(f"[outbound_call] Error fetching prompt: {e}")

    # Compute campaign id
    campaign_id = get_campaign_id(organisation_id, user_id)
    keys = q_keys(campaign_id)
    logging.info(f"[outbound_call] Campaign ID: {campaign_id}")

    # Fetch config unconditionally to get allyroid_voice
    allyroid_voice = "ash"
    db_welcome_message = None
    if organisation_id or user_id:
        query = {}
        if organisation_id: query["organisation_id"] = organisation_id
        if user_id: query["user_id"] = user_id
        
        cfg = orgcalls_collection.find_one(query, {"_id": 0}) or {}
        db_welcome_message = cfg.get("welcome_message")
        allyroid_voice = cfg.get("allyroid_voice", "ash")
        
    # Priority: User input > DB Config
    if not welcome_message:
        welcome_message = db_welcome_message
    logging.info(f"[outbound_call] Final Welcome message: {welcome_message}, Voice Model: {allyroid_voice}")
    
    # Final fallback to environment variable
    if not full_phone_number:
        full_phone_number = PLIVO_NUMBER
        logging.info(f"[outbound_call] Using env Plivo number: {full_phone_number}")
    
    # === CAMPAIGN TRACKING: Create campaign record ===
    try:
        tracking = get_tracking_service()
        events = get_event_service()
        
        # Create campaign record (now synchronous)
        tracking.create_campaign(
            campaign_id=campaign_id,
            org_id=organisation_id or "public",
            user_id=user_id or "nouser",
            total_numbers=len(final_leads),
            agent_number=full_phone_number or PLIVO_NUMBER,
            prompt_source=prompt_name
        )
        
        # Emit campaign created event
        events.emit_campaign_created(
            campaign_id=campaign_id,
            org_id=organisation_id or "public",
            user_id=user_id or "nouser",
            total_numbers=len(final_leads),
        )

        logging.info(f"[Campaign Tracking] Created campaign {campaign_id} with {len(final_leads)} numbers")
    except Exception as e:
        logging.warning(f"[Campaign Tracking] Failed to create campaign record: {e}")
        
    # Store queue config in Redis (for ALL calls in this queue)
    queue_config = {
        "welcome_message": welcome_message,
        "system_prompt": system_prompt, # Store the specific prompt for this campaign
        "full_phone_number": full_phone_number,
        "campaign_id": campaign_id,
        "organisation_id": organisation_id,
        "user_id": user_id,
        "voice": allyroid_voice,
        "provider": "plivo",
    }
    redis_client.setex(keys["config"], 172800, json.dumps(queue_config))

    # Reset ONLY this campaign's queue & push leads (as JSON strings)
    redis_client.delete(keys["queue"])
    
    # Serialize leads to JSON strings
    leads_json_list = [json.dumps(lead) for lead in final_leads]
    
    redis_client.rpush(keys["queue"], *leads_json_list)
    redis_client.expire(keys["queue"], 272800)
    
    # === CAMPAIGN TRACKING: Update status to running ===
    try:
        tracking.update_status(campaign_id, "running")
        events.emit_campaign_started(campaign_id, organisation_id, user_id)
    except Exception as e:
        logging.warning(f"[Campaign Tracking] Failed to update status: {e}")

    # Start campaign loop
    started = process_campaign_queue(campaign_id)
    
    if started:
        return {
            "status": "success",
            "message": f"Successfully queued {len(final_leads)} lead numbers. First call initiated.",
            "queued_count": len(final_leads),
            "plivo_number": full_phone_number,
            "organisation_id": organisation_id,
            "user_id": user_id
        }
    else:
        # If queue processing failed immediately (e.g. queue empty or all failed)
        return {
            "status": "success", 
            "message": f"Campaign created but no calls could be initiated immediately (check logs/status).",
            "queued_count": len(final_leads)
        }

def resume_campaign_service(campaign_id: str) -> dict:
    """
    Resume a paused campaign by restarting the call loop.
    """
    logging.info(f"[resume_campaign] Resuming campaign {campaign_id}")
    try:
        tracking = get_tracking_service()
        
        # 1. Update status to RUNNING
        tracking.update_status(campaign_id, "running")
        
        # 2. Trigger queue processing
        started = process_campaign_queue(campaign_id)
        
        if started:
             return {"status": "success", "message": "Campaign resumed and calls initiated."}
        else:
             return {"status": "success", "message": "Campaign resumed (but queue might be empty or finished)."}
        
    except Exception as e:
        logging.error(f"[resume_campaign] Error: {e}")
        return {"status": "error", "message": str(e)}

def process_campaign_queue(campaign_id: str) -> bool:
    """
    Process the campaign queue:
    1. Check status
    2. Pop number
    3. Try to call
    4. If fail, loop to next
    5. If success, return True
    6. If empty, mark complete and return False
    """
    try:
        tracking = get_tracking_service()
        keys = q_keys(campaign_id)
        
        # Check status from Redis directly
        status_key = f"campaign:{campaign_id}:status"
        current_status = redis_client.get(status_key)
        
        if current_status and current_status != "running":
            logging.info(f"[Campaign] Campaign {campaign_id} status is '{current_status}'. Stopping call loop.")
            return False

        # Guard against starting multiple calls concurrently if one is already in progress
        progress = tracking.get_progress(campaign_id)
        if progress and progress.get("in_progress", 0) > 0:
            logging.info(f"[Campaign-Plivo] Campaign {campaign_id} already has {progress.get('in_progress')} call in progress. Waiting for completion.")
            return False

        # Loop until successful call or queue empty
        while True:
            # Pop next item (could be JSON string or raw number string for backward compat)
            next_item_raw = redis_client.lpop(keys["queue"])
            
            if not next_item_raw:
                # Queue empty -> Campaign Finished
                logging.info(f"[Campaign] Queue empty for {campaign_id}. Marking as COMPLETED.")
                
                # Get final progress
                progress = tracking.get_progress(campaign_id)
                
                # Fetch org/user for event
                cfg_raw = redis_client.get(keys["config"])
                org_id = None
                user_id = None
                if cfg_raw:
                    cfg = json.loads(cfg_raw)
                    org_id = cfg.get("organisation_id")
                    user_id = cfg.get("user_id")

                tracking.update_progress(campaign_id, in_progress=0, pending=0)
                tracking.update_status(campaign_id, "completed")
                
                try:
                    events = get_event_service()
                    events.emit_campaign_completed(
                        campaign_id=campaign_id,
                        completed=progress.get("completed", 0),
                        failed=progress.get("failed", 0),
                        org_id=org_id,
                        user_id=user_id
                    )
                except Exception as e:
                    logging.warning(f"[Event] Failed to emit campaign completed: {e}")
                
                return False

            # Parse item
            try:
                # Try to parse as JSON (new format)
                lead_data = json.loads(next_item_raw)
                if isinstance(lead_data, dict):
                    next_number = lead_data.get("number")
                    lead_name = lead_data.get("name", "")
                else:
                    # Fallback if JSON is just a string? Unlikely but handling cases
                    next_number = str(lead_data)
                    lead_name = ""
            except json.JSONDecodeError:
                # Fallback to raw string (old format)
                next_number = next_item_raw
                lead_name = ""

            # Try to initiate call
            cfg_raw = redis_client.get(keys["config"])
            if cfg_raw:
                cfg = json.loads(cfg_raw)
                agent_plivo_number = cfg.get("full_phone_number") or PLIVO_NUMBER
                
                success = call_initiate(
                    next_number,
                    agent_plivo_number,
                    cfg.get("welcome_message"),
                    cfg.get("system_prompt"),
                    campaign_id=campaign_id,
                    lead_name=lead_name,
                    voice=cfg.get("voice", "ash")
                )
                
                if success:
                    logging.info(f"[Campaign] Call initiated successfully to {next_number}")
                    return True
                else:
                    logging.warning(f"[Campaign] Call failed to initiate to {next_number}. Trying next...")
                    # Update stats for the failed initiation attempt
                    tracking.increment_progress(campaign_id, failed_delta=1)
            else:
                logging.error(f"[Campaign] Config missing for {campaign_id}! Aborting.")
                return False

    except Exception as e:
        logging.error(f"[process_campaign_queue] Error: {e}")
        return False

def call_initiate(to_number, 
    agent_plivo_number, 
    welcome_message=None, 
    system_prompt=None, campaign_id: str | None = None, 
    lead_name: str = "", 
    voice: str = "ash",
    organisation_id: str | None = None,
    user_id: str | None = None

) -> bool:
    """
    Initiates a call from your Plivo number (from_number) to the user (to_number).
    Returns True if call successfully queued/started, False otherwise.
    """
    
    try:
        response = client.calls.create(
            from_=agent_plivo_number,   # Your Plivo phone number
            to_=to_number,     # The destination number
            answer_url=f"{PLIVO_ANSWER_URL}/plivo",
            hangup_url=PLIVO_HANGUP_URL,
            hangup_method="POST",
            answer_method="POST",
            caller_name="",   # Optional: Set the caller name
            
        )
        call_uuid = response.request_uuid if hasattr(response, 'request_uuid') else None
        
        if call_uuid:
            # Store config in Redis using call_uuid as key
            config_key = f"call_config:{call_uuid}"
            # print("Inside call initiate func---------")
            # print("organisation_id:",organisation_id)
            # print("user_id:",user_id)
            redis_client.setex(
                config_key,
                8600,  # expire after 2 hour
                json.dumps({
                    "welcome_message": welcome_message,
                    "system_prompt": system_prompt, 
                    "campaign_id": campaign_id,
                    "lead_name": lead_name,
                    "voice": voice,
                    "organisation_id": organisation_id,
                    "user_id": user_id,
                })
            )
            
            # === EVENT: Emit call started ===
            if campaign_id:
                try:
                    events = get_event_service()
                    events.emit_call_started(
                        campaign_id=campaign_id,
                        call_uuid=call_uuid,
                        to_number=to_number,
                        from_number=agent_plivo_number
                    )
                    
                    # Update campaign progress (increment in_progress)
                    tracking = get_tracking_service()
                    tracking.increment_progress(
                        campaign_id=campaign_id,
                        in_progress_delta=1
                    )
                except Exception as e:
                    logging.warning(f"[Event] Failed to emit call started: {e}")
            
            logging.info(f"Call initiated to {to_number}. Call UUID: {call_uuid}")
            return True
            
    except Exception as e:
        logging.error(f"Error initiating call to {to_number}: {str(e)}")
        
        # === EVENT: Emit call failed ===
        if campaign_id:
            try:
                events = get_event_service()
                events.emit_call_failed(
                    campaign_id=campaign_id,
                    call_uuid="N/A",
                    to_number=to_number,
                    error=str(e)
                )
            except Exception as ev_err:
                logging.warning(f"[Event] Failed to emit call failed: {ev_err}")
                
    return False

@router.post("/make-call-plivo", tags=["Calling-Plivo"])
def make_outbound_call(request: CallRequest):
    logging.info(f"[make-call-plivo] Initiating single call to {request.phone_number}")

    success = call_initiate(
        to_number=request.phone_number,
        agent_plivo_number=PLIVO_NUMBER,
        welcome_message=request.welcome_message,
        system_prompt=None,
        campaign_id=None,
        lead_name=request.lead_name or "",
        organisation_id=request.organisation_id,
        user_id=request.user_id,
    )

    if success:
        return {"status": "success", "message": f"Call initiated to {request.phone_number} via FreJun."}
    else:
        return {"status": "failed", "message": f"Failed to initiate call to {request.phone_number}. Check server logs."}


@router.post("/answer/plivo", tags=["Calling-Plivo"])
async def answer(request: Request):
    """
    Called by Plivo when the call first arrives.
    Starts full-session recording AND opens the realtime stream to your server.
    """
    form = await request.form()
    call_uuid = form.get("CallUUID")
    response = plivoxml.ResponseElement()

    # 1) Start full-session recording
    logging.info(f"Starting full session recording for call {call_uuid}")
    rec = plivoxml.RecordElement()
    rec.set_record_session(True)
    rec.set_max_length(3600)  # secondsm
    rec.set_callback_url(PLIVO_HANGUP_URL )  # Where to send the recording URL
    rec.set_callback_method("POST")

    rec.set_transcription_type("auto")
    rec.set_transcription_url(PLIVO_TRANSCRIPT_URL)
    rec.set_transcription_method("POST")
    response.add(rec)

    # 2) Open bidirectional streaming (μ-law 8kHz)
    # plivo_wss = os.getenv("PLIVO_WSS_URL")
    # plivo_wss = "wss://sari-intercausative-unecstatically.ngrok-free.dev/media-stream-plivo"
    server_base = os.getenv("SERVER_BASE_URL")
    if server_base:
        plivo_wss = f"{server_base.replace('https://', 'wss://').replace('http://', 'ws://')}/media-stream-plivo"
    else:
        plivo_wss = "wss://devallyraadksalescalling.pansophictech.com/media-stream-plivo"

    logging.info(f"Using Plivo WSS URL: {plivo_wss}")
    stream = plivoxml.StreamElement(
        plivo_wss,
        streamTimeout="86400",
        keepCallAlive=True,
        bidirectional=True,
        contentType="audio/x-mulaw;rate=8000",
        audioTrack="inbound",
    )
    response.add(stream)

    xml = response.to_string()
    return Response(content=xml, media_type="application/xml")


# Background MongoDB store
def store_call_data(call_data: dict, recording_url: str, transcript: str = "Transcript not available"):
    # Check if this specific call UUID already exists (Upsert logic)
    call_uuid = call_data.get("call_uuid")
    existing_record = calls_collection.find_one({"call_uuid": call_uuid})
    
    if existing_record:
        # Update existing record
        update_fields = {**call_data}
        if recording_url and recording_url != "No Recording Available":
            update_fields["recording_url"] = recording_url
        if transcript and transcript != "Transcript not available":
            update_fields["transcript"] = transcript
            
        calls_collection.update_one(
            {"call_uuid": call_uuid},
            {"$set": update_fields}
        )
        logging.info(f"Updated existing call record {call_uuid} in MongoDB.")
        return True
    else:
        # Insert new record
        campaign_id = call_data.get("campaign_id")
        to_number = call_data.get("to_number")
        from_number = call_data.get("from_number")
        
        attempt_number = 1
        is_reattempt = False
        
        if campaign_id and to_number:
            # Count previous calls for this specific campaign and number pair
            # We match strictly on campaign and to_number
            query = {
                "campaign_id": campaign_id,
                "to_number": to_number
            }
            previous_count = calls_collection.count_documents(query)
            attempt_number = previous_count + 1
            is_reattempt = previous_count > 0

        call_data_with_provider = {"provider": "plivo", **call_data}
        calls_collection.insert_one({
             **call_data_with_provider,
            "recording_url": recording_url,
            "transcript": transcript,
            "attempt_number": attempt_number,
            "is_reattempt": is_reattempt
        })
        logging.info(f"Stored NEW call {call_data.get('to_number')} in MongoDB. Attempt: {attempt_number}")
        return True


async def process_hangup_data(call_data: dict, recording_url: str | None,call_uuid: str):
    """Background process: upload to S3 (if recording exists) and store call data."""
    try:
        s3_url = None
        if recording_url:
            s3_url = await upload_url_to_s3(recording_url)
            duration_sec = call_data.get("duration", 0)
            #put duration_sec into redis queue
            redis_client.setex(
                f"call_duration:{call_uuid}",
                3600,
                json.dumps(duration_sec)
            )
            logging.info(f"Uploaded recording to S3: {s3_url}")
        else:
            logging.info(f"No recording URL provided for call {call_data.get('to_number')}")
            s3_url = "No Recording Available"

        # Pass the S3 URL (or placeholder) to storage
        success = store_call_data(call_data, s3_url)

        if success:
            logging.info(f"Successfully stored call data in Redis for {call_data['to_number']}")
        else:
            logging.error(f"Failed to store call data in Redis for {call_data['to_number']}")
    except Exception as e:
        logging.error(f"Error in background processing for {call_data['to_number']}: {e}")


@router.post("/hangup-plivo")
async def hangup(request: Request, background_tasks: BackgroundTasks):
    """
    Plivo calls this when the call ends. RecordUrl contains full session recording.
    """
    await asyncio.sleep(1)  # slight delay to ensure recording is ready
    form = await request.form()

    call_uuid = form.get("CallUUID")
    print("form:",form)
    # find campaign for this call
    call_cfg_raw = redis_client.get(f"call_config:{call_uuid}")
    print("call_uuid:",call_uuid)
    print("call_cfg_raw:",call_cfg_raw)
    campaign_id = None
    if call_cfg_raw:
        call_cfg = json.loads(call_cfg_raw)
        campaign_id = call_cfg.get("campaign_id")
        
    lead_name = call_cfg.get("lead_name", "") if call_cfg else ""
        
    # 2) Pull org/user from campaign config (if any)
    organisation_id = None
    user_id = None
    if campaign_id:
        # print("Campaign_id:",campaign_id)
        keys = q_keys(campaign_id)
        cfg_raw = redis_client.get(keys["config"])
        # print("cfg_raw:",cfg_raw)
        if cfg_raw:
            # print("Inside hanup func---------")
            # print("organisation_id:",organisation_id)
            # print("user_id:",user_id)
            cfg = json.loads(cfg_raw)
            organisation_id = cfg.get("organisation_id")
            user_id = cfg.get("user_id")
    recording_url = form.get("RecordUrl")

    call_data = {
        "call_uuid": form.get("CallUUID"),
        "campaign_id": campaign_id,            
        "organisation_id": organisation_id,     
        "user_id": user_id,     
        "from_number": form.get("From"),
        "to_number": form.get("To"),
        "direction": form.get("Direction"),
        "duration": int(form.get("RecordingDuration", 0)),
        "start_time": form.get("SessionStart"),
        "end_time": form.get("EndTime"),
        "hangup_cause": form.get("HangupCauseName", "Normal Hangup"),
        "hangup_cause": form.get("HangupCauseName", "Normal Hangup"),
        "caller_name": form.get("CallerName"),
        "lead_name": lead_name
    }
    logging.info(f"Call {call_data['to_number']} ended. Full recording: {recording_url}")
    
    # === EVENT: Emit call completed and update progress ===
    if campaign_id:
        try:
            # Prevent double-counting stats for the same CallUUID
            # Plivo might send multiple events or retries
            dedup_key = f"processed_stats:{call_data.get('call_uuid')}"
            is_new_event = redis_client.set(dedup_key, "1", ex=3600, nx=True)
            
            if not is_new_event:
                logging.warning(f"[Event] Duplicate hangup event for {call_data.get('call_uuid')}. Skipping stats update.")
            else:
                events = get_event_service()
                tracking = get_tracking_service()
                
                # Determine if call was successful or failed
                hangup_cause = call_data.get("hangup_cause", "")
                is_success = hangup_cause in ["Normal Hangup", "NORMAL_CLEARING", "ORIGINATOR_CANCEL", "End Of XML Instructions"]
                
                if is_success:
                    events.emit_call_completed(
                        campaign_id=campaign_id,
                        call_uuid=call_data.get("call_uuid", ""),
                        to_number=call_data.get("to_number", ""),
                        duration=call_data.get("duration", 0),
                        hangup_cause=hangup_cause
                    )
                    # Update progress (now synchronous)
                    tracking.increment_progress(
                        campaign_id=campaign_id,
                        completed_delta=1,
                        in_progress_delta=-1
                    )
                else:
                    events.emit_call_failed(
                        campaign_id=campaign_id,
                        call_uuid=call_data.get("call_uuid", ""),
                        to_number=call_data.get("to_number", ""),
                        error=hangup_cause
                    )
                    # Update progress (now synchronous)
                    tracking.increment_progress(
                        campaign_id=campaign_id,
                        failed_delta=1,
                        in_progress_delta=-1
                    )
        except Exception as e:
            logging.warning(f"[Event] Failed to emit call completed: {e}")
    
    # Always process call data even if no recording (e.g. busy/failed calls)
    # If we have a recording URL, good. If not, we still want to log the call attempt.
    logging.info(f"Scheduling background task to process call data (Recording: {bool(recording_url)})")
    background_tasks.add_task(process_hangup_data, call_data, recording_url,call_uuid)
    # Trigger next number from Redis queue
    # pop next number from the SAME campaign queue
    if campaign_id:
        try:
            process_campaign_queue(campaign_id)
        except Exception as e:
            logging.error(f"[hangup] Error in next call loop: {e}")
                
    return Response(content="OK", status_code=200)


# @router.get("/calls/numbers")
# def get_stored_numbers():
#     """
#     Fetch all numbers currently in Redis queue.
#     """
#     numbers = redis_client.lrange(QUEUE_KEY, 0, -1)
#     return {"stored_numbers": numbers, "count": len(numbers)}


@router.post("/action/transcription")
async def plivo_transcription(request: Request, background_tasks: BackgroundTasks):
    await asyncio.sleep(3)  # slight delay to ensure recording is ready
    form = await request.form()
    # print("Under plivo transcription endpoint:", form)
    event = form.get("Event")
    if event != "Transcription":
        return Response("IGNORED", 200)

    call_uuid = form.get("call_uuid")
    transcript = form.get("transcription") or ""
    duration = form.get("duration")
    rate = form.get("transcription_rate")
    charge = form.get("transcription_charge")

    logging.info(f"[Transcription] call_uuid={call_uuid} len={len(transcript)}")

    # Update existing doc by call_uuid
    query = {"call_uuid": call_uuid}


    calls_collection.update_one(
        query,
        {
            "$set": {
                "transcript": transcript if transcript else "Transcript empty",
                "transcription_meta": {
                    "duration": duration,
                    "rate": rate,
                    "charge": charge,
                },
                "transcription_received_at": datetime.utcnow(),
            }
        },
        upsert=False,  # we expect the row to exist from RecordStop/S3 flow
    )

    # === BACKGROUND ANALYSIS ===
    # Trigger LLM analysis to classify the outcome
    # We check if transcript exists and has some substance
    #find recording url
    call = calls_collection.find_one(
        {"call_uuid": call_uuid},
        {"_id": 0, "recording_url": 1}
        )

    recording_url = call.get("recording_url") if call else None

    # for _ in range(5):
    #     # obs_raw = redis_client.get(f"call_obs:{call_uuid}")

    #     if obs_raw:
    #         obs_metrics = json.loads(obs_raw)
    #         print("obs_raw:",obs_raw)
    #         break

    if call_uuid and transcript and len(transcript.strip()) > 10:
        logging.info(f"Queuing background analysis for call {call_uuid}")
        background_tasks.add_task(analyze_call_outcome, call_uuid, transcript,recording_url)

    return Response("OK", 200)
