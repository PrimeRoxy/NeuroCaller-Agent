import asyncio
from datetime import datetime
import json
from fastapi import Request, Response,APIRouter
import httpx
from plivo import RestClient
import os
from dotenv import load_dotenv
from plivo import plivoxml
import logging
from call.service import upload_url_to_s3
from fastapi import BackgroundTasks
from pymongo import MongoClient
from core.config import redis_client
load_dotenv()

 
PLIVO_AUTH_ID = os.getenv("PLIVO_AUTH_ID")
PLIVO_AUTH_TOKEN = os.getenv("PLIVO_AUTH_TOKEN")
PLIVO_NUMBER = os.getenv("PLIVO_NUMBER")
PLIVO_ANSWER_URL = os.getenv("PLIVO_ANSWER_URL")
PLIVO_ACTION_URL = os.getenv("PLIVO_ACTION_URL")
PLIVO_HANGUP_URL = os.getenv("PLIVO_HANGUP_URL")
PLIVO_TRANSCRIPT_URL = f"{PLIVO_ACTION_URL}/transcription"
WSS_URL = os.getenv("WSS_URL")
router = APIRouter()
client = RestClient(auth_id=PLIVO_AUTH_ID, auth_token=PLIVO_AUTH_TOKEN)


# MongoDB connection
mongo_client = MongoClient(os.getenv("MONGO_URI"))
db = mongo_client["calls_db"]
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

def outbound_call(numbers: str,organisation_id: str | None = None, user_id: str | None = None):
    """
    Store numbers in Redis queue and start first call.
    """
    nums = [n.strip() for n in numbers.split(",") if n.strip()]
    if not nums:
        return {"status": "failed", "msg": "No numbers provided"}
    # Always define local variables before usage
    full_phone_number = None
    welcome_message = None
    extracted_file_text = None
    # Compute campaign id even if org/user are missing
    campaign_id = get_campaign_id(organisation_id, user_id)
    keys = q_keys(campaign_id)

    
    if organisation_id and user_id:
        cfg = orgcalls_collection.find_one(
            {"organisation_id": organisation_id},
            {"_id": 0}
        ) or {}
        
        # Extract and store globally
        welcome_message = cfg.get("welcome_message")
        print(welcome_message)
        phone_number = cfg.get("phone_number", "")
        if phone_number:
            full_phone_number = f"{phone_number}"
            print(f"Agent Number: {full_phone_number}")
        uploaded_resources = cfg.get("uploaded_resources", [])
        file_texts = [resource.get("file_data", "") for resource in uploaded_resources if resource.get("file_data")]
        extracted_file_text = "\n\n".join(file_texts) if file_texts else None
        
    # Store queue config in Redis (for ALL calls in this queue)
    queue_config = {
        "welcome_message": welcome_message,
        "extracted_file_text": extracted_file_text,
        "full_phone_number": full_phone_number,
        "campaign_id": campaign_id,
        "organisation_id": organisation_id,
        "user_id": user_id,
    }
    redis_client.setex(keys["config"], 172800, json.dumps(queue_config))

    # Reset ONLY this campaign’s queue & push numbers
    redis_client.delete(keys["queue"])
    redis_client.rpush(keys["queue"], *nums)
    redis_client.expire(keys["queue"], 272800)

    # Start first call
    first_number = redis_client.lpop(keys["queue"])
    if first_number:
        agent_plivo_number = full_phone_number if full_phone_number else PLIVO_NUMBER
        return call_initiate(first_number,agent_plivo_number,welcome_message,extracted_file_text,campaign_id=campaign_id)
    return {"status": "failed", "msg": "No valid numbers"}


def call_initiate(to_number,agent_plivo_number,welcome_message = None,extracted_file_text=None,campaign_id: str | None = None,):
    """
    Initiates a call from your Plivo number (from_number) to the user (to_number).
    Uses the default Answer URL set in the Plivo console.
    """
    # agent_plivo_number = full_phone_number if full_phone_number else PLIVO_NUMBER

    try:
        response = client.calls.create(
            from_=agent_plivo_number,   # Your Plivo phone number
            to_=to_number,     # The destination number
            answer_url=PLIVO_ANSWER_URL,
            hangup_url=PLIVO_HANGUP_URL,
            hangup_method="POST",
            answer_method="POST",
            caller_name="Sales Agent",   # Optional: Set the caller name
            
        )
        call_uuid = response.request_uuid if hasattr(response, 'request_uuid') else None
        # Store config in Redis using call_uuid as key
        if call_uuid:
            config_key = f"call_config:{call_uuid}"
            redis_client.setex(
                config_key,
                8600,  # expire after 2 hour
                json.dumps({
                    "welcome_message": welcome_message,
                    "extracted_file_text": extracted_file_text,
                    "campaign_id": campaign_id,
                })
            )
        if call_uuid:
            logging.info(f"Call initiated to {to_number}. Call UUID: {call_uuid}")
            return f"Call initiated to {to_number} with Call UUID: {call_uuid}"
    except Exception as e:
        logging.error(f"Error initiating call to {to_number}: {str(e)}")
    return f"Please ensure the phone number is in the correct format, such as +[country code][number], and try again."
    

@router.post("/answer")
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
    rec.set_max_length(3600)  # seconds
    rec.set_callback_url(PLIVO_HANGUP_URL )  # Where to send the recording URL
    rec.set_callback_method("POST")

    rec.set_transcription_type("auto")
    rec.set_transcription_url(PLIVO_TRANSCRIPT_URL)
    rec.set_transcription_method("POST")
    response.add(rec)

    # 2) Open bidirectional streaming (μ-law 8kHz)
    stream = plivoxml.StreamElement(
        WSS_URL,
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
    calls_collection.insert_one({
         **call_data,
        "recording_url": recording_url,
        "transcript": transcript
    })
    logging.info(f"Stored call {call_data['to_number']} in MongoDB.")
    return True


async def process_hangup_data(call_data: dict, recording_url: str):
    """Background process: upload to S3 and store transcript & summary in Redis."""
    try:
        s3_url = await upload_url_to_s3(recording_url)
        logging.info(f"Uploaded recording to S3: {s3_url}")
        success = store_call_data(call_data, s3_url)

        if success:
            logging.info(f"Successfully stored call data in Redis for {call_data['to_number']}")
        else:
            logging.error(f"Failed to store call data in Redis for {call_data['to_number']}")
    except Exception as e:
        logging.error(f"Error in background processing for {call_data['to_number']}: {e}")


@router.post("/hangup")
async def hangup(request: Request, background_tasks: BackgroundTasks):
    """
    Plivo calls this when the call ends. RecordUrl contains full session recording.
    """
    await asyncio.sleep(1)  # slight delay to ensure recording is ready
    form = await request.form()
    call_uuid = form.get("CallUUID")
    print(form)
    # find campaign for this call
    call_cfg_raw = redis_client.get(f"call_config:{call_uuid}")
    campaign_id = None
    if call_cfg_raw:
        call_cfg = json.loads(call_cfg_raw)
        campaign_id = call_cfg.get("campaign_id")
        
    # 2) Pull org/user from campaign config (if any)
    organisation_id = None
    user_id = None
    if campaign_id:
        keys = q_keys(campaign_id)
        cfg_raw = redis_client.get(keys["config"])
        if cfg_raw:
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
        "caller_name": form.get("CallerName")
    }
    logging.info(f"Call {call_data['to_number']} ended. Full recording: {recording_url}")
    if recording_url not in (None, ""):
        logging.info(f"Scheduling background task to process recording for call {recording_url} ")
        background_tasks.add_task(process_hangup_data, call_data, recording_url)
    # Trigger next number from Redis queue
    # pop next number from the SAME campaign queue
    if campaign_id:
        keys = q_keys(campaign_id)
        next_number = redis_client.lpop(keys["queue"])
        if next_number:
            cfg_raw = redis_client.get(keys["config"])
            if cfg_raw:
                cfg = json.loads(cfg_raw)
                agent_plivo_number = cfg.get("full_phone_number") or PLIVO_NUMBER
                call_initiate(
                    next_number,
                    agent_plivo_number,
                    cfg.get("welcome_message"),
                    cfg.get("extracted_file_text"),
                    campaign_id=campaign_id,
                )
    return Response(content="OK", status_code=200)


@router.get("/calls/data")
def get_call_data():
    """
    Fetch all stored call data from MongoDB.
    """
    records = list(calls_collection.find({}, {"_id": 0}))  # exclude Mongo _id
    return {"calls": records, "count": len(records)}

@router.post("/action/transcription")
async def plivo_transcription(request: Request):
    await asyncio.sleep(3)  # slight delay to ensure recording is ready
    form = await request.form()
    print(form)
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

    return Response("OK", 200)
