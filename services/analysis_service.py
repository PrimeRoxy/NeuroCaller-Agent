import os
import json
import logging
import traceback
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pymongo import MongoClient
from google import genai
from google.genai import types
import httpx
import asyncio
from services.salesforce_service import upsert_lead_by_phone, ensure_custom_fields_exist
from services.observability import Observability
from core.config import redis_client
from services.usage_cost_tracker import  UsageCostTracker
from sentiment.sentiment import _get_sentiments

load_dotenv()
agent_id = os.getenv("SALES_AGENT_ID")
SCHEDULER_URL = os.getenv("SCHEDULER_URL")
GPT_REAL_TIME_MODEL = "gpt-realtime-mini"
GEMINI_FLASH_MODEL = "gemini-flash-latest"
openai_analysis_model = os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-4.1-mini")

# Initialize Logging
logger = logging.getLogger("CallAnalysis")
logger.setLevel(logging.INFO)

# Initialize MongoDB (Separate connection to avoid circular imports)
MONGO_URI = os.getenv("MONGO_URI")
try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client.get_database()
    calls_collection = db["calls"]
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")

# Global Queue for CRM Sync
CRM_SYNC_QUEUE = asyncio.Queue()
_crm_worker_task = None

async def crm_sync_worker():
    """
    Background worker to process CRM sync tasks from the queue.
    """
    logger.info("[CRM Worker] Starting CRM Sync Worker...")
    from services.config_service import ConfigService
    while True:
        try:
            task_data = await CRM_SYNC_QUEUE.get()
            call_uuid = task_data.get("call_uuid")
            
            if not call_uuid:
                CRM_SYNC_QUEUE.task_done()
                continue
                
            logger.info(f"[CRM Worker] Processing sync for {call_uuid}")
            
            try:
                # Retrieve fresh data from DB to ensure we have latest config/status
                call_doc = calls_collection.find_one({"call_uuid": call_uuid})
                if not call_doc:
                    logger.warning(f"[CRM Worker] Call {call_uuid} not found in DB.")
                    CRM_SYNC_QUEUE.task_done()
                    continue
                # task_payload = {
                #     "call_uuid": call_uuid,
                #     "outcome": outcome,
                #     "reason": reason,
                #     "email": email_address,
                #     "follow_up_required": follow_up_required,
                #     "call_status": hangup_cause,
                #     "total_price":usage_tracker.total_price,
                #     "sales_stage":sales_stage,
                #     "recording_url":recording_url,
                #     "transcript":transcript

                # }
                org_id = call_doc.get("organisation_id")
                phone = call_doc.get("to_number")
                outcome = task_data.get("outcome") # Use passed outcome/reason/email from analysis
                reason = task_data.get("reason")
                call_status = task_data.get("call_status")
                total_price = task_data.get("total_price")
                sales_stage = task_data.get("sales_stage")
                recording_url = task_data.get("recording_url")
                transcript = task_data.get("transcript")
                email_address = task_data.get("email")

                user_id = call_doc.get("user_id") # Assuming user_id is available in call_doc

                if org_id and phone:
                    # print("--------------")
                    # print("Got the org_id and phone")
                    # print("org_id:",org_id)
                    # print("phone:",phone)
                    config = ConfigService.find_config(org_id) or {}
                
                    crm_provider = config.get("crm_provider", "").lower()
                    if crm_provider == "salesforce":
                        # Updated to handle nested credentials object
                        sf_creds = config.get("salesforce_creds", {})
                        allyroid_name = config.get("allyroid_name")
                        salesforce_client_id = sf_creds.get("client_id")
                        salesforce_client_secret = sf_creds.get("client_secret")
                        salesforce_domain = sf_creds.get("domain")
                      
                        if salesforce_client_id and salesforce_client_secret:
                            # --- Prepare Extended Data ---
                            # 1. Map Outcome to Status
                            status_map = {
                                "Qualified": "Qualified",
                                "Follow-up required": "Working - Contacted",
                                "Not interested": "Closed - Not Converted",
                                "Unreachable": "Open - Not Contacted"
                            }
                            sf_status = status_map.get(outcome, "Open - Not Contacted")
                            
                            # 2. Extract Lead Details
                            lead_name = call_doc.get("lead_name", "Lead")
                            if not lead_name or not lead_name.strip():
                                lead_name = "Lead"
                                
                            agent_number = call_doc.get("from_number", "Unknown")
                            duration = call_doc.get("duration", 0)
                            campaign_id = call_doc.get("campaign_id")

                            # print("agent_number:",agent_number)
                            # print("duration:",duration)
                            # print("campaign_id:",campaign_id)
                            # 3. Calculate Attempts
                            # Count how many times we've called this number in this campaign (or total if no campaign)
                            query_attempts = {"to_number": phone}
                            if campaign_id:
                                query_attempts["campaign_id"] = campaign_id
                            attempts = calls_collection.count_documents(query_attempts)
                            # print("after attempts")
                            # 4. Success/Status Logic (Next Best Action)
                            nba = call_doc.get("next_recommended_action")
                            if not nba:
                                nba = "N/A"
                                if outcome == "Qualified":
                                    nba = "Close Deal / Send Contract"
                                elif outcome == "Follow-up required":
                                    nba = "Call back next time"
                                elif outcome == "Not interested":
                                    nba = "Remove from list"
                                elif outcome == "Unreachable":
                                    nba = "Retry later"
                            # print("after if not nba")
                            # Limit to 255 characters for Salesforce compatibility
                            if nba and len(nba) > 255:
                                nba = nba[:252] + "..."
                                
                            # 5. Follow-ups
                            # print("before follow up time")
                            follow_up_time = call_doc.get("scheduled_callback_at")
                            # print("follow_up_time") 
                            # Payload
                            sf_data = {
                                "Company": "N/A", 
                                "LastName": lead_name,
                                "Status": sf_status,
                                "Phone": phone,
                                # Custom Fields (Mapped as requested)
                                # "Lead_Owner__c": user_id,
                                "Agent_Number__c": agent_number,
                                "Call_Attempts__c": attempts,
                                "Duration_Seconds__c": duration,
                                "Next_Best_Action__c": nba,
                                "Follow_Up_Required__c": "Yes" if task_data.get("follow_up_required") else "No",
                                "Outcome__c": outcome,
                                "Outcome_Reason__c": reason,
                                "Call_Status__c":call_status,
                                "Sales_Stage__c":sales_stage,
                                "Total_Price__c":total_price,
                                "Recording_URL__c":recording_url,
                                "Transcript__c":transcript,
                                "Time__c":datetime.utcnow().isoformat()
                            }
                            # print("Sf_data:",sf_data)
                            if email_address:
                                sf_data["Email"] = email_address
                            
                            # Perform Upsert (Non-blocking)
                            try:
                                loop = asyncio.get_running_loop()
                                
                                # 1. Ensure custom fields exist first (run in executor as it's blocking)
                                await loop.run_in_executor(
                                    None, 
                                    ensure_custom_fields_exist, 
                                    salesforce_client_id, 
                                    salesforce_client_secret,
                                    salesforce_domain
                                )
                                
                                # 2. Perform the actual upsert
                                await loop.run_in_executor(
                                    None, 
                                    upsert_lead_by_phone, 
                                    phone, 
                                    sf_data, 
                                    salesforce_client_id, 
                                    salesforce_client_secret,
                                    salesforce_domain
                                )
                                
                                # Update Status to SYNCED
                                calls_collection.update_one(
                                    {"call_uuid": call_uuid},
                                    {
                                        "$set": {
                                            "crm_sync": "synced",
                                            "crm_sync_date": datetime.utcnow()
                                        }
                                    }
                                )
                                logger.info(f"[CRM Worker] Sync SUCCESS for {call_uuid}")
                            except Exception as sf_err:
                                logger.warning(f"[CRM Worker] Salesforce sync bypassed/failed for {call_uuid}: {sf_err}")
                                calls_collection.update_one(
                                    {"call_uuid": call_uuid},
                                    {"$set": {"crm_sync": "bypassed"}}
                                )
                        else:
                            error_msg = f"Missing Salesforce credentials for org {org_id}"
                            logger.warning(f"[CRM Worker] {error_msg}")
                            calls_collection.update_one(
                                {"call_uuid": call_uuid},
                                {"$set": {"crm_sync": "failed", "crm_sync_error": error_msg}}
                            )
                    else:
                        # Not salesforce
                        pass

            except Exception as e:
                logger.error(f"[CRM Worker] Task failed for {call_uuid}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                
                # Update Status to FAILED
                calls_collection.update_one(
                    {"call_uuid": call_uuid},
                    {
                        "$set": {
                            "crm_sync": "failed", 
                            "crm_sync_error": str(e)
                        }
                    }
                )
            
            finally:
                CRM_SYNC_QUEUE.task_done()

        except Exception as e:
            logger.error(f"[CRM Worker] Critical Error in worker loop: {e}")
            await asyncio.sleep(5) # Prevent tight loop if queue breaks

# Global Queue for Scheduling
SCHEDULING_QUEUE = asyncio.Queue()
_scheduling_worker_task = None

async def trigger_direct_schedule(organisation_id: str, user_id: str, session_id: str, prompt: str):
    """
    Directly triggers the scheduling agent via the specified endpoint.
    """
    url = f"{SCHEDULER_URL}/api/v1/schedule/agent/direct"
    
    payload = {
        "user_id": user_id,
        "prompt": prompt,
        "organization_id": organisation_id,
        "agent_id": agent_id,
        "session_id": session_id
    }
    
    try:
        async with httpx.AsyncClient() as client:
            logger.info(f"[Scheduling Worker] Sending direct schedule request to {url}")
            response = await client.post(url, json=payload, timeout=30.0)
            if response.status_code != 200:
                logger.error(f"[Scheduling Worker] Direct schedule failed: {response.text}")
            else:
                logger.info(f"[Scheduling Worker] Direct schedule success: {response.json()}")
    except Exception as e:
        logger.error(f"[Scheduling Worker] Direct schedule exception: {e}")

async def scheduling_worker():
    """
    Background worker to process Scheduling tasks.
    """
    logger.info("[Scheduling Worker] Starting Scheduling Worker...")
    while True:
        try:
            task_data = await SCHEDULING_QUEUE.get()
            call_uuid = task_data.get("call_uuid")
            print("Task_data:",task_data)
            if not call_uuid:
                SCHEDULING_QUEUE.task_done()
                continue
                
            logger.info(f"[Scheduling Worker] Processing task for {call_uuid}")
            
            try:
                call_doc = calls_collection.find_one({"call_uuid": call_uuid})
                if not call_doc:
                    logger.warning(f"[Scheduling Worker] Call {call_uuid} not found.")
                    SCHEDULING_QUEUE.task_done()
                    continue

                scheduled_time_str = task_data.get("time")
                email_address = task_data.get("email")
                title = task_data.get("title") or "Sales Meeting"

                org_id = call_doc.get("organisation_id")
                user_id = call_doc.get("user_id")

                # print("scheduled_time_str:", scheduled_time_str)
                # print("email_address:", email_address)
                # print("title:", title)
                # print("org_id:", org_id)
                # print("user_id:", user_id)
                # print("task_data:", task_data)
                # print("call_doc:", call_doc)
                if org_id and user_id:
                    session_id_derived = f"session_{user_id}_{org_id}"
                    
                    # Format prompt with required Title, Email, Time
                    schedule_parts = []
                    if title:
                        schedule_parts.append(f"title '{title}'")
                    if scheduled_time_str:
                        schedule_parts.append(f"at {scheduled_time_str}")
                    if email_address:
                        schedule_parts.append(f"with email {email_address}")
                        
                    schedule_details = " ".join(schedule_parts)
                    schedule_prompt = f"Schedule a meeting with {schedule_details} for one hour."
                    
                    logger.info(f"[Scheduling Worker] Triggering agent for {call_uuid}: {schedule_prompt}")
                    response = await trigger_direct_schedule(
                        organisation_id=org_id,
                        user_id=user_id,
                        session_id=session_id_derived,
                        prompt=schedule_prompt
                    )
                    logger.info(f"[Scheduling Worker] Agent response for {call_uuid}: {response}")
            except Exception as e:
                logger.error(f"[Scheduling Worker] Failed: {e}")
                
            finally:
                SCHEDULING_QUEUE.task_done()

        except Exception as e:
            logger.error(f"[Scheduling Worker] Critical Error: {e}")
            await asyncio.sleep(5)

def ensure_worker_running():
    global _crm_worker_task, _scheduling_worker_task
    try:
        loop = asyncio.get_running_loop()
        
        # CRM Worker
        if _crm_worker_task is None or _crm_worker_task.done():
            _crm_worker_task = loop.create_task(crm_sync_worker())
            logger.info("[Internal] CRM Worker task started.")
            
        # Scheduling Worker
        if _scheduling_worker_task is None or _scheduling_worker_task.done():
            _scheduling_worker_task = loop.create_task(scheduling_worker())
            logger.info("[Internal] Scheduling Worker task started.")
            
    except Exception as e:
        logger.warning(f"[Internal] Could not start workers: {e}")

# Initialize Gemini Client
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
client = None
if GOOGLE_API_KEY:
    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Gemini client: {e}")

# Initialize OpenAI Client
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai_client = None
if OPENAI_API_KEY:
    try:
        from openai import AsyncOpenAI
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI client: {e}")

async def generate_transcript_from_audio(audio_url: str, call_uuid: "str") -> str:
    """
    Downloads the audio from the given URL and uses Gemini to generate a transcription.
    """
    if not client:
        logger.error("Gemini client not initialized. Cannot transcribe.")
        return "Transcript not available"

    if not audio_url or audio_url == "No Recording Available":
        return "Transcript not available"

    temp_file_path = None
    uploaded_file = None
    try:
        logger.info(f"[Transcription] Downloading audio from {audio_url}")
        
        # Download the audio file to a temporary file
        import tempfile
        import httpx
        
        async with httpx.AsyncClient(verify=False) as http_client:
            response = await http_client.get(audio_url, timeout=60.0)
            if response.status_code != 200:
                logger.error(f"[Transcription] Failed to download audio: HTTP {response.status_code}")
                return "Transcript not available"
            audio_bytes = response.content

        # Create a temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_file:
            temp_file.write(audio_bytes)
            temp_file_path = temp_file.name

        logger.info(f"[Transcription] Uploading file to Gemini: {temp_file_path}")
        
        # Upload using genai client (run in executor since it's blocking)
        loop = asyncio.get_running_loop()
        uploaded_file = await loop.run_in_executor(
            None,
            lambda: client.files.upload(
                file=temp_file_path,
                config=types.UploadFileConfig(mime_type="audio/mp3")
            )
        )
        
        logger.info(f"[Transcription] Uploaded to Gemini: {uploaded_file.name}. Waiting for processing...")
        
        # Wait for file processing if needed
        import time
        # Wait for file processing with error resilience
        for _ in range(30):
            try:
                file_info = await loop.run_in_executor(None, lambda: client.files.get(name=uploaded_file.name))
                if file_info.state.name == "ACTIVE":
                    break
                elif file_info.state.name == "FAILED":
                    raise Exception("Gemini file processing failed")
            except Exception as poll_err:
                logger.warning(f"[Transcription] File status check error: {poll_err}")
            await asyncio.sleep(2)
        
        logger.info("[Transcription] Generating transcription using Gemini...")
        prompt = ( "Please transcribe the following audio recording of a phone call word for word. " "There are always exactly two speakers: Agent and Customer. " "Identify each speaker based on the conversation context and format every spoken turn using exactly these labels: " "'Agent:' and 'Customer:'. " "Each speaker's speech must start on a new line. " "Do not use any other speaker labels, names, or variations. " "Do not add a preamble, introduction, summary, notes, timestamps, or explanations. " "Return only the transcript in the following format:\n\n" "Agent: [agent's speech]\n" "Customer: [customer's speech]\n\n" "Continue this format for the entire conversation." )
        
        response = None
        last_error = None
        max_retries = 4

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[Transcription] Requesting Gemini ({GEMINI_FLASH_MODEL}, attempt {attempt}/{max_retries})...")
                response = await loop.run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=GEMINI_FLASH_MODEL,
                        contents=[uploaded_file, prompt]
                    )
                )
                if response and hasattr(response, 'text') and response.text:
                    break
            except Exception as g_err:
                last_error = g_err
                logger.warning(f"[Transcription] Gemini model '{GEMINI_FLASH_MODEL}' attempt {attempt} failed: {g_err}")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)

        if not response or not hasattr(response, 'text') or not response.text:
            if last_error:
                raise last_error
            logger.warning("[Transcription] Gemini returned empty transcription across all retries")
            return "Transcript not available"
        
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            obs = Observability(GEMINI_FLASH_MODEL)
            obs.record(response.usage_metadata)
            try:
                await obs.calculate_cost(GEMINI_FLASH_MODEL)
            except Exception as obs_err:
                logger.warning(f"[Transcription Observability] Cost calculation failed: {obs_err}")
            try:
                transcription_metrics = obs.metrics()
                await asyncio.to_thread(
                    redis_client.setex,
                    f"call_transcription_obs:{call_uuid}",
                    3600,
                    json.dumps(transcription_metrics)
                )
            except Exception as e:
                logger.error(f"[Transcription] Failed to put metrics to Redis: {e}")

            logger.info(f"[Transcription Observability] Metrics: {obs.metrics()}")
        
        transcript_text = response.text
        if transcript_text:
            logger.info(f"[Transcription] Generated transcript successfully ({len(transcript_text)} chars using {GEMINI_FLASH_MODEL})")
            return transcript_text.strip()
        else:
            logger.warning("[Transcription] Gemini returned empty transcription")
            return "Transcript not available"

    except Exception as e:
        logger.error(f"[Transcription] Error generating transcript: {e}", exc_info=True)
        return "Transcript not available"

    finally:
        # Clean up temp file
        if temp_file_path:
            try:
                os.remove(temp_file_path)
            except Exception:
                pass
        # Clean up Gemini file
        if uploaded_file:
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: client.files.delete(name=uploaded_file.name))
            except Exception as delete_err:
                logger.warning(f"[Transcription] Failed to delete Gemini file {uploaded_file.name}: {delete_err}")

async def analyze_call_outcome(call_uuid: str, transcript: str, audio_url=None):
    """
    Analyzes the call transcript using LLM to determine the outcome.
    Updates the call record in MongoDB with 'outcome' and 'outcome_reason'.
    
    This function is designed to run as a background task.
    """
    if not openai_client:
        logger.error("OpenAI client not initialized. Skipping analysis.")
        return

    if not transcript or len(transcript.strip()) < 10:
        logger.warning(f"Transcript too short for analysis. Call UUID: {call_uuid}")
        return

    logger.info(f"[Analysis] Starting outcome classification for {call_uuid}")

    # Define the classification prompt
   
    try:
        if not audio_url:
            logger.warning(f"[Analysis] No audio_url for {call_uuid}, skipping sentiment analysis")
            sentiments = {}
        else:
            sentiments = await _get_sentiments(audio_url,call_uuid)

        # update_fields["sentiments"] = sentiments
        logger.info(f"Sentiments:",sentiments)

        analysis_result = sentiments


        reason = analysis_result.get("reason", "")
        follow_up_required = analysis_result.get("follow_up_required", False)
        scheduled_time_str = analysis_result.get("follow_up_date_time") or analysis_result.get("scheduled_time")
        email_address = analysis_result.get("email")
        meeting_title = analysis_result.get("title")
        
        next_recommended_action = analysis_result.get("next_recommended_action")
        follow_up_date_time = analysis_result.get("follow_up_date_time")
        sales_stage = analysis_result.get("sales_stage")
        confidence = analysis_result.get("confidence")
        background_noise = analysis_result.get("background_noise")
        emotional_tone = analysis_result.get("emotional_tone")
        explanation = analysis_result.get("explanation")
        
        # Update MongoDB
        update_fields = {
            "transcript": transcript,

            "next_recommended_action": next_recommended_action,
            "follow_up_date_time": follow_up_date_time,
            "sales_stage": sales_stage,
            "follow_up_required": follow_up_required,
            "scheduled_callback_at": scheduled_time_str,
            "analysis_date": datetime.utcnow(),
            "confidence": confidence,
            "background_noise": background_noise,
            "emotional_tone": emotional_tone,
            "explanation": explanation
        }
        
        # print("update_fields:",update_fields)
        # update_fields.update(sentiments)
        usage_tracker = UsageCostTracker(call_uuid,redis_client,calls_collection)
        
        await usage_tracker.load_metrics()
        await usage_tracker.calculate_provider_cost()
        await usage_tracker.put_metrics_to_mongodb()


        if email_address:
            update_fields["extracted_email"] = email_address

        calls_collection.update_one(
            {"call_uuid": call_uuid},
            {"$set": update_fields}
        )
        
        logger.info(f"[Analysis] Completed for {call_uuid}| Stage: {sales_stage}")


        # === SCHEDULING LOGIC ===
        # Only trigger scheduling if a specific time is provided.
        # We avoid defaulting to 24h to prevent "random" callbacks that annoy users.
        # === SCHEDULING LOGIC ===
        
        # Fetch campaign details once if we need to schedule
        # call_doc = None
        # print("-----inside analyze outcome function-----")
        # print("Email address:", email_address)
        # print("scheduled time:", scheduled_time_str)
        # print("call_doc:", call_doc)
        # print("-----inside analyze outcome function-----")
        if scheduled_time_str or email_address:
            call_doc = calls_collection.find_one({"call_uuid": call_uuid})

        # 1. Internal Redial/Callback (Needs valid time)
        # Only trigger internal callback if a specific time is provided.
        if scheduled_time_str:
            try:
                # Determine run_at time
                run_at = None
                try:
                    # Parse ISO string (handle Z if present)
                    run_at = datetime.fromisoformat(scheduled_time_str.replace("Z", "+00:00"))
                except ValueError:
                    logger.warning(f"Invalid ISO time '{scheduled_time_str}', skipping callback.")
                
                if run_at and call_doc:
                    campaign_id = call_doc.get("campaign_id")
                    to_number = call_doc.get("to_number")
                    provider = call_doc.get("provider")
                    from_number = call_doc.get("from_number") or call_doc.get("agent_number") or call_doc.get("virtual_number")
                    lead_name = call_doc.get("lead_name", "")
                    
                    if campaign_id and to_number:
                        from services.scheduler_service import schedule_callback
                        schedule_callback(
                            campaign_id=campaign_id,
                            to_number=to_number,
                            run_at=run_at,
                            provider=provider,
                            from_number=from_number,
                            lead_name=lead_name
                        )

            except Exception as e:
                logger.error(f"[Analysis] Failed to schedule internal callback: {e}")

        # 2. External Meeting/Email Scheduling (Time AND Email)
        # 2. External Meeting/Email Scheduling (Time AND Email)
        # Trigger if we have a time AND an email (implying intent to connect/schedule)
        if (scheduled_time_str and email_address) and call_doc:
            try:
                # Queue the task
                task_payload = {
                    "call_uuid": call_uuid,
                    "time": scheduled_time_str,
                    "email": email_address,
                    "title": meeting_title
                }
                ensure_worker_running()
                SCHEDULING_QUEUE.put_nowait(task_payload)
                logger.info(f"[Analysis] Queued scheduling task for {call_uuid}")
                
            except Exception as e:
                logger.error(f"[Analysis] Failed to queue scheduling task: {e}")
        elif follow_up_required:
                logger.info(f"[Analysis] 'Follow-up required' for {call_uuid} but no specific time found. Skipping auto-callback.")

        # === CRM SYNC LOGIC (Background Queue) ===
        try:
            # 1. Update status to PENDING immediately
            calls_collection.update_one(
                {"call_uuid": call_uuid},
                {"$set": {"crm_sync": "pending"}}
            )

            # 2. Queue the task
            hangup_cause =  calls_collection.find_one(
                {"call_uuid": call_uuid},
                {"hangup_cause": 1, "_id": 0}
            )
            recording_url = calls_collection.find_one(
                {"call_uuid": call_uuid},
                {"recording_url":1, "_id":0}
            )


            task_payload = {
                "call_uuid": call_uuid,
                "reason": reason,
                "email": email_address,
                "follow_up_required": follow_up_required,
                "call_status": hangup_cause.get("hangup_cause",""),
                "total_price":usage_tracker.total_price,
                "sales_stage":sales_stage,
                "recording_url":recording_url.get("recording_url",""),
                "transcript":transcript

            }
            
            # Ensure worker is running
            ensure_worker_running()
            
            # Add to queue
            CRM_SYNC_QUEUE.put_nowait(task_payload)
            logger.info(f"[Analysis] Queued Salesforce sync for {call_uuid}")

        except Exception as e:
            logger.error(f"[Analysis] Failed to queue CRM sync: {e}")
            # Mark as failed if we couldn't even queue it
            calls_collection.update_one(
                {"call_uuid": call_uuid},
                {"$set": {"crm_sync": "failed", "crm_sync_error": f"Queueing failed: {e}"}}
            )
            
    # except json.JSONDecodeError:
    #     logger.error(f"[Analysis] Failed to parse LLM response for {call_uuid}: {response.text}")
        
    except Exception as e:
        logger.error(f"[Analysis] Error analyzing call {call_uuid}: {traceback.format_exc()}")

