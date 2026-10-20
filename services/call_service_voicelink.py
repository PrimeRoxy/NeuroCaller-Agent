import asyncio
from datetime import datetime
import json
from fastapi import Request, Response, APIRouter
from fastapi.responses import JSONResponse
import requests
from pydantic import BaseModel
from typing import Optional
import os
from dotenv import load_dotenv
import logging
from services.uploader import upload_url_to_s3
from fastapi import BackgroundTasks
from pymongo import MongoClient
from core.config import redis_client
from services.prompt_session_service import PromptSessionService
from services.analysis_service import analyze_call_outcome
from services.campaign_tracking_service import get_campaign_tracking_service
from services.task_event_service import get_task_event_service

# Tracking and event services imported at the top

load_dotenv()
VOICELINK_NUMBER = os.getenv("VOICELINK_NUMBER")

router = APIRouter()


# MongoDB connection
mongo_client = MongoClient(os.getenv("MONGO_URI"))
db = mongo_client.get_database()
calls_collection = db["calls"]
orgcalls_collection = db["orgcalls"]

class CallRequest(BaseModel):
    phone_number: str
    lead_name: Optional[str] = ""
    welcome_message: Optional[str] = None
    prompt_name: Optional[str] = None
    organisation_id: Optional[str] = "manual"
    user_id: Optional[str] = "api_user"
    agent_voice: Optional[str] = None
    allyroid_voice: Optional[str] = None
    voice: Optional[str] = None
    agent_language: Optional[str] = None

@router.post("/make-call-vl", tags=["Calling-Voicelink"])
def make_outbound_call(request: CallRequest):
    """
    Lightweight API endpoint to initiate a single outbound call via VoiceLink.
    Calls outbound_call directly, bypassing MongoDB-dependent campaign pipeline.
    """
    logging.info(f"[make-call] Initiating call to {request.phone_number}")

    requested_voice = request.agent_voice or request.allyroid_voice or request.voice
    if not requested_voice and (request.organisation_id or request.user_id):
        query = {}
        if request.organisation_id: query["organisation_id"] = request.organisation_id
        if request.user_id: query["user_id"] = request.user_id
        cfg = orgcalls_collection.find_one(query, {"_id": 0}) or {}
        requested_voice = cfg.get("agent_voice") or cfg.get("allyroid_voice") or cfg.get("voice")

    # Give the single-call test endpoint a real campaign id so the live
    # dashboard can route and scope it (org/user default to manual/api_user).
    campaign_id = get_campaign_id(request.organisation_id, request.user_id)

    # Store call config in Redis so the websocket handler can pick it up
    success = execute_outbound_call(
        to_number=request.phone_number,
        agent_voicelink_number="",
        welcome_message=request.welcome_message,
        system_prompt=None,
        campaign_id=campaign_id,
        lead_name=request.lead_name or "",
        agent_voice=requested_voice,
        agent_language=request.agent_language,
        organisation_id=request.organisation_id,
        user_id=request.user_id,
    )

    if success:
        return {
            "status": "success",
            "message": f"Call initiated to {request.phone_number} via VoiceLink.",
        }
    else:
        return {
            "status": "failed",
            "message": f"Failed to initiate call to {request.phone_number}. Check server logs.",
        }

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


def resume_campaign_service(campaign_id: str) -> dict:
    """
    Resume a paused campaign by restarting the call loop.
    """
    logging.info(f"[resume_campaign] Resuming campaign {campaign_id}")
    try:
        tracking = get_campaign_tracking_service()
        
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
        tracking = get_campaign_tracking_service()
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
            logging.info(f"[Campaign-VoiceLink] Campaign {campaign_id} already has {progress.get('in_progress')} call in progress. Waiting for completion.")
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
                    events = get_task_event_service()
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
                agent_voicelink_number = cfg.get("full_phone_number") or VOICELINK_NUMBER
                
                agent_voice = cfg.get("agent_voice") or cfg.get("allyroid_voice") or cfg.get("voice")
                agent_language = cfg.get("agent_language") or cfg.get("language")

                success = execute_outbound_call(
                    next_number,
                    agent_voicelink_number,
                    cfg.get("welcome_message"),
                    cfg.get("system_prompt"),
                    campaign_id=campaign_id,
                    lead_name=lead_name,
                    agent_voice=agent_voice,
                    agent_language=agent_language,
                    organisation_id=cfg.get("organisation_id"),
                    user_id=cfg.get("user_id"),
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

def get_voicelink_token() -> str:
    """Gets cached VoiceLink token or authenticates to get a new one."""
    token_key = "voicelink_auth_token"
    token = redis_client.get(token_key)
    if token:
        return token.decode("utf-8") if isinstance(token, bytes) else token
        
    username = os.getenv("VOICELINK_USERNAME")
    password = os.getenv("VOICELINK_PASSWORD")
    token = os.getenv("VOICELINK_TOKEN")
    if not username or not password:
        logging.error("VOICELINK_USERNAME or VOICELINK_PASSWORD not set")
        return ""
        
    try:
        import requests
        login_url = os.getenv("VOICELINK_LOGIN_URL", "https://voicebroad.elisiontec.com/api/login")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {token}"
        }
        resp = requests.post(login_url, data={"user": username, "password": password}, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        
        new_token = data.get("access_token", "")
        expires_in = data.get("expires_in", 1800)
        
        if new_token:
            redis_client.setex(token_key, max(10, int(expires_in) - 60), new_token)
            return new_token
    except Exception as e:
        logging.error(f"Failed to authenticate with VoiceLink: {e}")
        
    return ""

def outbound_call(
    leads: list[dict] | str, 
    organisation_id: str | None = None, 
    user_id: str | None = None, 
    voicelink_number: str | None = None, 
    prompt_name: str | None = None,
    welcome_message: str | None = None,
    voice: str | None = None,
):
    """
    Store numbers in Redis queue and start first call.
    Also creates campaign tracking record and emits events.
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

    logging.info(f"[outbound_call] Voicelink number (from chat): {voicelink_number}")
    logging.info(f"[outbound_call] Org: {organisation_id}, User: {user_id}")
    logging.info(f"[outbound_call] Prompt Name: {prompt_name}")
    logging.info(f"[outbound_call] Welcome Message: {welcome_message}")
    
    if not final_leads:
        logging.warning("[outbound_call] No leads provided")
        return {"status": "failed", "msg": "No leads provided"}
    
    # Initialize variables
    full_phone_number = voicelink_number
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

    # Priority: User input > DB Config
    db_welcome_message = None
    db_agent_voice = None
    agent_language = None
    if organisation_id or user_id:
        query = {}
        if organisation_id: query["organisation_id"] = organisation_id
        if user_id: query["user_id"] = user_id
        cfg = orgcalls_collection.find_one(query, {"_id": 0}) or {}
        db_welcome_message = cfg.get("welcome_message")
        db_agent_voice = cfg.get("agent_voice") or cfg.get("allyroid_voice") or cfg.get("voice")
        agent_language = cfg.get("agent_language") or cfg.get("language") or "hindi"

    if not welcome_message:
        welcome_message = db_welcome_message
        
    agent_voice = voice or db_agent_voice

    logging.info(f"[outbound_call] Final Welcome message: {welcome_message}")
    logging.info(f"[outbound_call] Final Allyroid Voice: {agent_voice}")
    
    # Final fallback to environment variable
    if not full_phone_number:
        full_phone_number = VOICELINK_NUMBER
        logging.info(f"[outbound_call] Using env Voicelink number: {full_phone_number}")
    
    # === CAMPAIGN TRACKING: Create campaign record ===
    try:
        tracking = get_campaign_tracking_service()
        events = get_task_event_service()
        
        # Create campaign record (now synchronous)
        tracking.create_campaign(
            campaign_id=campaign_id,
            org_id=organisation_id or "public",
            user_id=user_id or "nouser",
            total_numbers=len(final_leads),
            agent_number=full_phone_number or VOICELINK_NUMBER,
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
        "agent_voice": agent_voice, # Store the dynamic voice model
        "allyroid_voice": agent_voice,
        "agent_language": agent_language, # Store the dynamic language
        "full_phone_number": full_phone_number,
        "campaign_id": campaign_id,
        "organisation_id": organisation_id,
        "user_id": user_id,
        "provider": "voicelink",
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
            "voicelink_number": full_phone_number,
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

def call_initiate(
    to_number,
    agent_phone_number,
    welcome_message=None,
    system_prompt=None,
    campaign_id: str | None = None,
    lead_name: str = "",
    voice: str | None = None,
    organisation_id: str | None = None,
    user_id: str | None = None
) -> bool:
    if not voice and (organisation_id or user_id):
        query = {}
        if organisation_id: query["organisation_id"] = organisation_id
        if user_id: query["user_id"] = user_id
        cfg = orgcalls_collection.find_one(query, {"_id": 0}) or {}
        voice = cfg.get("agent_voice") or cfg.get("allyroid_voice") or cfg.get("voice")

    return execute_outbound_call(
        to_number=to_number,
        agent_voicelink_number=agent_phone_number,
        welcome_message=welcome_message,
        system_prompt=system_prompt,
        campaign_id=campaign_id,
        lead_name=lead_name,
        agent_voice=voice,
        organisation_id=organisation_id,
        user_id=user_id
    )

def execute_outbound_call(to_number, agent_voicelink_number, welcome_message=None, system_prompt=None, campaign_id: str | None = None, lead_name: str = "", agent_voice: str | None = None, agent_language: str | None = None, organisation_id: str | None = None, user_id: str | None = None) -> bool:
    """
    Initiates a call to the user (to_number) using VoiceLink APIs.
    Returns True if call successfully queued/started, False otherwise.
    """
    try:
        import uuid
        import requests
        
        # VoiceLink API Configuration
        voicelink_api_url = os.getenv("VOICELINK_ADD_LEAD_URL", "https://voicebroad.elisiontec.com/api/add-lead").strip().strip('"').strip("'")
        
        # Authenticate and obtain token
        list_id = os.getenv("VOICELINK_LIST_ID", "260330180329").strip().strip('"').strip("'")
        voicelink_token = os.getenv("VOICELINK_TOKEN", "").strip().strip('"').strip("'")
        
        # Generate a unique UUID for internal tracking
        call_uuid = str(uuid.uuid4())
        
        headers = {
            "Authorization": f"Bearer {voicelink_token}",
            "Accept": "application/json"
        }
        
        wss_url = (os.getenv("WSS_URL") or "wss://sari-intercausative-unecstatically.ngrok-free.dev/media-stream-vl/").strip().strip('"').strip("'")
        # if wss_url.rstrip("/").endswith("/media-stream-vl"):
        #     wss_url = wss_url.rstrip("/").rsplit("/media-stream-vl", 1)[0] + "/media-stream-vl-male/"
        hangup_url = (os.getenv("VOICELINK_HANGUP_URL") or "https://sari-intercausative-unecstatically.ngrok-free.dev/hangup-vl").strip().strip('"').strip("'")
        
        logging.info(f"[outbound_call] Using WSS URL: {wss_url}")
        logging.info(f"[outbound_call] Using hangup URL: {hangup_url}")
        comments_data = {
            "web_socket_url": wss_url,
            "status_callback": hangup_url,
            "call_uuid": call_uuid
        }

        # Resolve agent_voicelink_number and remove any quotes
        prov_num = str(agent_voicelink_number or os.getenv("VOICELINK_NUMBER", "917969460176")).strip().strip('"').strip("'")
        
        payload = {
            "phone_number": to_number,
            "list_id": list_id,
            "add_to_hopper": "Y",
            "source": "NA",
            "province": prov_num,
            "comments": json.dumps(comments_data),
        }
        
        # print(f"🔍 [outbound_call] FULL PAYLOAD: {json.dumps(payload, indent=2)}")
        
        if voicelink_token:
            response = requests.post(voicelink_api_url, data=payload, headers=headers)
            print("====================================")
            print(f"VoiceLink add-lead API response: {response.text}")
            print("====================================")
            response.raise_for_status()
            logging.info(f"VoiceLink add-lead API successful for {to_number}")
        
        if call_uuid:
            # Store config in Redis using call_uuid as key
            config_key = f"call_config:{call_uuid}"
            redis_client.setex(
                config_key,
                8600,  # expire after 2 hour
                json.dumps({
                    "welcome_message": welcome_message,
                    "system_prompt": system_prompt,
                    "allyroid_voice": agent_voice,
                    "agent_language": agent_language,
                    "campaign_id": campaign_id,
                    "lead_name": lead_name,
                    "organisation_id": organisation_id,
                    "user_id": user_id
                })
            )
            
            # === EVENT: Emit call started ===
            if campaign_id:
                try:
                    events = get_task_event_service()
                    events.emit_call_started(
                        campaign_id=campaign_id,
                        call_uuid=call_uuid,
                        to_number=to_number,
                        from_number=agent_voicelink_number
                    )
                    
                    # Update campaign progress (increment in_progress)
                    tracking = get_campaign_tracking_service()
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
                events = get_task_event_service()
                events.emit_call_failed(
                    campaign_id=campaign_id,
                    call_uuid="N/A",
                    to_number=to_number,
                    error=str(e)
                )
            except Exception as ev_err:
                logging.warning(f"[Event] Failed to emit call failed: {ev_err}")
                
    return False
    
# Background MongoDB store
def store_call_data(call_data: dict, recording_url: str, transcript: str = "Transcript not available"):
    # Check if this specific call UUID already exists (Upsert logic)
    call_uuid = call_data.get("call_uuid")
    existing_record = calls_collection.find_one({"call_uuid": call_uuid})
    
    campaign_id = call_data.get("campaign_id") or (existing_record.get("campaign_id") if existing_record else None)
    to_number = call_data.get("to_number") or (existing_record.get("to_number") if existing_record else None)

    # Determine attempt_number and is_reattempt
    if existing_record and existing_record.get("attempt_number") is not None:
        attempt_number = existing_record.get("attempt_number")
        is_reattempt = existing_record.get("is_reattempt", False)
    else:
        attempt_number = 1
        is_reattempt = False
        if campaign_id and to_number:
            # Count previous calls for this specific campaign and number pair, excluding current call
            query = {
                "campaign_id": campaign_id,
                "to_number": to_number,
            }
            if call_uuid:
                query["call_uuid"] = {"$ne": call_uuid}
            previous_count = calls_collection.count_documents(query)
            attempt_number = previous_count + 1
            is_reattempt = previous_count > 0

    if existing_record:
        # Update existing record
        update_fields = {
            **call_data,
            "attempt_number": attempt_number,
            "is_reattempt": is_reattempt,
        }
        if recording_url and recording_url != "No Recording Available":
            update_fields["recording_url"] = recording_url
        if transcript and transcript != "Transcript not available":
            update_fields["transcript"] = transcript
            
        calls_collection.update_one(
            {"call_uuid": call_uuid},
            {"$set": update_fields}
        )
        logging.info(f"Updated existing call record {call_uuid} in MongoDB. Attempt: {attempt_number}")
        return True
    else:
        # Insert new record
        call_data_with_provider = {"provider": "voicelink", **call_data}
        calls_collection.insert_one({
             **call_data_with_provider,
            "recording_url": recording_url,
            "transcript": transcript,
            "attempt_number": attempt_number,
            "is_reattempt": is_reattempt
        })
        logging.info(f"Stored NEW call {call_data.get('to_number')} in MongoDB. Attempt: {attempt_number}")
        return True


async def process_hangup_data(call_data: dict, recording_url: str | None):
    """Background process: upload to S3 (if recording exists) and store call data."""
    call_identifier = call_data.get('call_uuid') or call_data.get('to_number') or 'unknown'
    try:
        s3_url = None
        print("---------------in process hangup data-----------------------------")
        print("Recording url:", recording_url)
        print("--------------------------------------------")
        if recording_url:
            s3_url = await upload_url_to_s3(recording_url)
            duration_sec = call_data.get("duration", 0)

            redis_client.setex(
                f"call_duration:{call_data.get('call_uuid')}",
                3600,
                json.dumps(duration_sec)
            )
            logging.info(f"Uploaded recording to S3: {s3_url}")
        else:
            logging.info(f"No recording URL provided for call {call_identifier}")
            s3_url = "No Recording Available"

        # Pass the S3 URL (or placeholder) to storage
        success = store_call_data(call_data, s3_url)

        if success:
            logging.info(f"Successfully stored call data for {call_identifier}")
            
            # Initiate background transcription and analysis
            if s3_url and s3_url != "No Recording Available" and recording_url:
                from services.analysis_service import generate_transcript_from_audio, analyze_call_outcome
                logging.info(f"Initiating transcript generation for {call_identifier}")
                print("---------------in process hangup data-----------------------------")
                print("Recording url:", recording_url)
                print("--------------------------------------------")
                # Fetch transcript
                transcript = await generate_transcript_from_audio(recording_url,call_data.get('call_uuid'))
                if transcript and transcript != "Transcript not available":
                    calls_collection.update_one(
                        {"call_uuid": call_data.get("call_uuid")},
                        {"$set": {"transcript": transcript}}
                    )
                
                # Run outcome analysis based on transcript
                if transcript and transcript != "Transcript not available" and len(transcript) > 10:
                    logging.info(f"Transcript generated for {call_identifier}. Submitting for analysis...")
                    asyncio.create_task(analyze_call_outcome(call_data.get("call_uuid"), transcript,recording_url))
                else:
                    logging.warning(f"Could not generate transcript for {call_identifier}")
        else:
            logging.error(f"Failed to store call data for {call_identifier}")
    except Exception as e:
        logging.error(f"Error in background processing for {call_identifier}: {e}")


@router.post("/hangup-vl")
async def hangup(request: Request, background_tasks: BackgroundTasks):
    """
    Greeter/VoiceLink calls this webhook (Call Log Post API) when the call ends.
    Supports both Greeter field names and legacy VoiceLink/Plivo field names.
    
    Greeter Call Log Post API fields (configured in Greeter admin panel):
      - Recording: direct recording file URL
      - Customer_Number: the customer's phone number
      - Virtual_Number: the DID/virtual number used
      - Receiver_Number: agent's phone number
      - Duration: total call duration (including ring time)
      - Answer_Duration: answered/talk duration only
      - Status: Answer / Noanswer / Busy / Failed
      - Call_Type: Inbound / Outbound
      - Unique_ID: VoiceLink's unique call identifier
      - Call_Date: timestamp of the call
    """
    await asyncio.sleep(1)  # slight delay to ensure recording is ready
    
    # Read raw body for debugging
    raw_body = await request.body()
    print(f"📞 Raw Body ({len(raw_body)} bytes): {raw_body[:2000]}")
    
    # Try multiple data extraction methods
    data = {}
    content_type = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            data = dict(form)
            print("📞 Parsed multipart/form-data successfully.")
        elif "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            data = dict(form)
            print("📞 Parsed x-www-form-urlencoded successfully.")
        else:
            data = await request.json()
    except Exception as e:
        print(f"📞 Failed to parse form/json from request: {e}")
        # Try JSON body as fallback if form parsing failed or no form
        try:
            data = json.loads(raw_body) if raw_body else {}
        except Exception:
            pass

    if not data:
        print("📞 Empty payload received, ignoring.")
        return {"status": "ignored", "message": "Empty payload"}

    print("========================================")
    print("📞 Hangup Webhook Final Payload:")
    print(json.dumps(dict(data) if hasattr(data, '__iter__') else data, indent=2, default=str))
    print("========================================")
    
    # -------------------------------------------------------
    # 1) Resolve call_uuid: try multiple sources
    # -------------------------------------------------------
    # Parse `custom` as JSON string if necessary
    custom_field = data.get("custom", {})
    if isinstance(custom_field, str):
        try:
            custom_data = json.loads(custom_field)
        except Exception:
            custom_data = {}
    elif isinstance(custom_field, dict):
        custom_data = custom_field
    else:
        custom_data = {}

    # Priority: custom.call_uuid > CallUUID > Unique_ID > voicelink mapping
    call_uuid = (
        custom_data.get("call_uuid")
        or data.get("CallUUID")
        or data.get("call_uuid")
    )
    
    # Greeter's own unique call ID (from Call Log Post API)
    voicelink_unique_id = data.get("Unique_ID") or data.get("Call_UniqueId")
    
    # If we don't have our internal call_uuid, try to look it up via VoiceLink's ID
    if not call_uuid and voicelink_unique_id:
        mapped_uuid = redis_client.get(f"voicelink_call:{voicelink_unique_id}")
        if mapped_uuid:
            call_uuid = mapped_uuid.decode("utf-8") if isinstance(mapped_uuid, bytes) else mapped_uuid
            logging.info(f"[hangup] Resolved call_uuid={call_uuid} from VoiceLink Unique_ID={voicelink_unique_id}")
        else:
            # Use VoiceLink's ID as fallback call_uuid
            call_uuid = voicelink_unique_id
            logging.warning(f"[hangup] No mapping found for VoiceLink Unique_ID={voicelink_unique_id}, using as call_uuid")
    
    # FALLBACK: If we still have no call_uuid (empty payload from VoiceLink),
    # try to recover the last active call_uuid from Redis
    if not call_uuid:
        last_call_uuid = redis_client.get("last_active_call_uuid")
        if last_call_uuid:
            call_uuid = last_call_uuid.decode("utf-8") if isinstance(last_call_uuid, bytes) else last_call_uuid
            logging.info(f"[hangup] Recovered call_uuid={call_uuid} from last_active_call_uuid fallback")
            # Clear it so it's not re-used for a future hangup
            redis_client.delete("last_active_call_uuid")
        else:
            logging.warning("[hangup] No call_uuid available from any source - payload was empty and no fallback found")
    
    logging.info(f"[hangup] call_uuid={call_uuid}, voicelink_unique_id={voicelink_unique_id}")
    
    # -------------------------------------------------------
    # 2) Fetch campaign config from Redis
    # -------------------------------------------------------
    call_cfg_raw = redis_client.get(f"call_config:{call_uuid}") if call_uuid else None
    campaign_id = None
    call_cfg = {}
    if call_cfg_raw:
        call_cfg = json.loads(call_cfg_raw)
        campaign_id = call_cfg.get("campaign_id")
        
    # Retrieve lead_name from Redis config if available
    lead_name = call_cfg.get("lead_name", "")
        
    # Pull org/user from campaign config (if any)
    organisation_id = None
    user_id = None
    if campaign_id:
        keys = q_keys(campaign_id)
        cfg_raw = redis_client.get(keys["config"])
        if cfg_raw:
            cfg = json.loads(cfg_raw)
            organisation_id = cfg.get("organisation_id")
            user_id = cfg.get("user_id")
    
    # -------------------------------------------------------
    # 3) Extract recording URL (Greeter: "Recording", legacy: "RecordingURL")
    # -------------------------------------------------------
    recording_url = (
        data.get("Recording")
        or data.get("RecordingURL")
        or data.get("RecordUrl")
        or data.get("recording")
        or data.get("recording_url")
    )
    print("--------------------------------------------")
    print("Recording url:", recording_url)
    print("--------------------------------------------")
    # -------------------------------------------------------
    # 4) Extract duration (Greeter sends Duration & Answer_Duration)
    # -------------------------------------------------------
    try:
        duration_val = (
            data.get("Answer_Duration")    # Greeter: actual talk time (preferred)
            or 0
        )
        duration = int(duration_val)
    except (ValueError, TypeError):
        duration = 0
    
    # Also capture total duration separately if available
    try:
        total_duration = int(data.get("Duration") or 0)
    except (ValueError, TypeError):
        total_duration = 0
    
    # -------------------------------------------------------
    # 5) Extract call status (Greeter: "Status" = Answer/Noanswer/Busy/Failed)
    # -------------------------------------------------------
    greeter_status = data.get("Status")
    call_status = data.get("CallStatus") or greeter_status or data.get("Hangupcode") or "Normal Hangup"
    
    # Map Greeter status values to our hangup_cause format
    if greeter_status:
        status_mapping = {
            "ANSWER": "Normal Hangup",
            "NO ANSWER": "NO ANSWER",
            "BUSY": "BUSY",
            "Failed": "CALL_FAILED",
            "Cancel": "ORIGINATOR_CANCEL",
        }
        call_status = status_mapping.get(greeter_status, greeter_status)

    # -------------------------------------------------------
    # 6) Build call_data dict
    # -------------------------------------------------------
    call_data = {
        "call_uuid": call_uuid,
        "voicelink_unique_id": voicelink_unique_id,
        "campaign_id": campaign_id,            
        "organisation_id": organisation_id,     
        "user_id": user_id,     
        "from_number": (
            data.get("Virtual_Number") or data.get("From_Number")
        ),
        "to_number": (
            data.get("Customer_Number") or data.get("To_number")
        ),
        "direction": (
            data.get("Call_Type")
        ),
        "duration": duration,
        "total_duration": total_duration,
        "start_time": (
            data.get("Call_Date") or data.get("CallStartDateTime")
        ),
        "end_time": data.get("CallEndDateTime") or data.get("EndTime"),
        "hangup_cause": call_status,
        "lead_name": lead_name
    }
    logging.info(f"📞 Call {call_data['to_number']} ended. Status: {call_status}. Recording: {recording_url}")
    
    # === EVENT: Emit call completed and update progress ===
    if campaign_id:
        try:
            # Prevent double-counting stats for the same call
            dedup_key = f"processed_stats:{call_data.get('call_uuid')}"
            is_new_event = redis_client.set(dedup_key, "1", ex=3600, nx=True)
            
            if not is_new_event:
                logging.warning(f"[Event] Duplicate hangup event for {call_data.get('call_uuid')}. Skipping stats update.")
            else:
                events = get_task_event_service()
                tracking = get_campaign_tracking_service()
                
                # Determine if call was successful or failed
                hangup_cause = call_data.get("hangup_cause", "")
                is_success = hangup_cause in [
                    "Normal Hangup", "NORMAL_CLEARING", "BUSY",
                    "End Of XML Instructions", "ANSWER"  # Greeter "Answer" status = successful
                ]
                
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
    logging.info(f"Scheduling background task to process call data (Recording: {bool(recording_url)})")
    background_tasks.add_task(process_hangup_data, call_data, recording_url)
    
    # Trigger next number from Redis queue
    if campaign_id:
        try:
            process_campaign_queue(campaign_id)
        except Exception as e:
            logging.error(f"[hangup] Error in next call loop: {e}")
                
    return Response(content="OK", status_code=200)


def get_voicelink_numbers() -> dict:
    """
    Fetch dynamic DID numbers from VoiceLink DID API.
    Docs: https://voicelink.elisiontec.com/api-references/did-1/list-did
    Endpoint: POST https://greeter.co.in/api/get-did-list (or VOICELINK_DID_URL)
    """
    pass
    # did_url = (os.getenv("VOICELINK_DID_URL") or "https://greeter.co.in/api/get-did-list")
    # token = (os.getenv("VOICELINK_DID_TOKEN") or os.getenv("VOICELINK_TOKEN") or "")
    # if not token:
    #     token = get_voicelink_token()

    # if not token:
    #     logging.warning("[VoiceLink][Numbers] No VoiceLink token available to fetch DID list.")
    #     return {"numbers": []}

    # def _fetch(tok: str):
    #     headers = {
    #         "Authorization": f"Bearer {tok}",
    #         "Content-Type": "application/json",
    #         "Accept": "application/json"
    #     }
    #     return requests.post(did_url, headers=headers, json={}, timeout=10)

    # try:
    #     response = _fetch(token)
    #     if response.status_code == 401:
    #         # Token might be expired, attempt to refresh
    #         logging.info("[VoiceLink][Numbers] Received 401, refreshing token...")
    #         token_key = "voicelink_auth_token"
    #         try:
    #             redis_client.delete(token_key)
    #         except Exception:
    #             pass
    #         new_token = get_voicelink_token()
    #         if new_token and new_token != token:
    #             response = _fetch(new_token)

    #     response.raise_for_status()
    #     res_json = response.json()
    #     raw_list = res_json.get("data") if isinstance(res_json, dict) else []
    #     numbers = []
    #     if isinstance(raw_list, list):
    #         for item in raw_list:
    #             if isinstance(item, dict):
    #                 num = item.get("did_number")
    #                 if num:
    #                     numbers.append(str(num).strip())
    #             elif isinstance(item, (str, int)):
    #                 numbers.append(str(item).strip())
    #     return {"numbers": numbers}
    # except Exception as exc:
    #     logging.warning(f"[VoiceLink][Numbers] Failed to fetch VoiceLink numbers from {did_url}: {exc}")
    #     return {"numbers": []}


@router.get("/voicelink/numbers", tags=["Calling-Voicelink"])
async def voicelink_numbers():
    try:
        result = await asyncio.to_thread(get_voicelink_numbers)
        return JSONResponse(status_code=200, content=result)
    except Exception as exc:
        logging.exception(f"[VoiceLink][Numbers] Error={exc}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(exc)}
        )
