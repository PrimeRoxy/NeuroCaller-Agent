import time, json
from pathlib import Path
import os
from pymongo import MongoClient
from .observability import client, Observability
from google import genai
import logging
import asyncio
from io import BytesIO
from mutagen.mp3 import MP3
from mutagen.wave import WAVE
import aiohttp
import time
from google.genai import types
from core.config import redis_client
from pydantic import ValidationError
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, ConfigDict, Field
from pydantic import BaseModel
from typing import Optional


class SentimentResult(BaseModel):
    next_recommended_action: str
    follow_up_date_time: Optional[str] = None
    sales_stage: str
    follow_up_required: bool
    reason: str
    scheduled_time: Optional[str] = None
    email: Optional[str] = None
    title: Optional[str] = None
    confidence: str
    background_noise: str
    emotional_tone: str
    explanation: str


logger = logging.getLogger(__name__)


# India Standard Time (UTC+05:30). Voicelink is an Indian telephony provider and
# callers speak in IST ("call me at 5pm", "kal subah 10 baje"), so every callback
# time must be computed and returned in IST.
IST = timezone(timedelta(hours=5, minutes=30))

# The prompt's "current time" MUST be resolved per-call (ideally from the call's
# actual end time), not frozen at import. We inject it at request time by replacing
# this placeholder, so the LLM always anchors relative times like "after 15 minutes"
# to when the call really ended instead of when the server process started.
REF_TIME_PLACEHOLDER = "__REFERENCE_TIME_IST__"


def _resolve_reference_time_ist(call_doc: dict | None) -> datetime:
    """Best "current time" to anchor relative callback requests, in IST.

    Prefers the call's end time (CallEndDateTime, when the user disconnected) so
    "call me in 15 minutes" is measured from the real hang-up moment. Falls back
    to now() if the call record has no usable end time.
    """
    raw_end = None
    if call_doc:
        raw_end = call_doc.get("end_time") or call_doc.get("CallEndDateTime")

    if raw_end:
        parsed = None
        # Epoch seconds (int/float or numeric string)
        try:
            parsed = datetime.fromtimestamp(float(raw_end), IST)
        except (ValueError, TypeError, OSError):
            parsed = None
        # ISO / common provider string formats
        if parsed is None and isinstance(raw_end, str):
            s = raw_end.strip().replace("Z", "+00:00")
            fmts = [None, "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S",
                    "%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"]
            for fmt in fmts:
                try:
                    parsed = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
        if parsed is not None:
            # Naive timestamps from an Indian provider are already IST wall-clock.
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=IST)
            return parsed.astimezone(IST)

    return datetime.now(IST)




GENERATE_SENTIMENTS  = f"""
You are a Quality Assurance Specialist for a sales team.
Your task is to analyze a phone call audio and extract key sales metadata,
customer emotional tone, confidence, and call-quality information.

Current Time (IST): {REF_TIME_PLACEHOLDER}

*Sales Stage Classification:**

Determine the lead's current sales stage based on the overall conversation and,
most importantly, the latest meaningful action or outcome. Choose the stage that
best represents where the lead currently stands in the sales process, rather
than an earlier stage they may have passed through.

Choose EXACTLY one value from the following list:

- "Contact Attempted"
- "Connected"
- "Qualified"
- "Interested"
- "Product Information Shared"
- "Pricing Shared"
- "Proposal Requested"
- "Proposal Sent"
- "Demo Scheduled"
- "Demo Completed"
- "Follow-up Pending"
- "Callback Requested"
- "Negotiation"
- "Decision Pending"
- "Converted"
- "On Hold"
- "No Response"
- "Not Interested"
- "Closed"

**Callback & Follow-up Scheduling:**

If the Lead Status is "Follow-up required" OR "Qualified":

1. Identify if the user stated a time, either absolute or relative:
   - Absolute: "call me at 5pm", "kal subah 10 baje", "sham ko call karna".
   - Relative: "after 15 minutes", "in 2 hours", "thodi der baad", "same time tomorrow".

2. Calculate the callback time as an IST ISO 8601 string that ALWAYS includes the
   +05:30 offset (e.g. "2026-10-01T18:30:00+05:30"), computed from Current Time (IST):
   - For a relative request, ADD the stated duration to Current Time
     (e.g. "after 15 minutes" => Current Time + 15 minutes).
   - For "same time tomorrow", keep the same clock time on the next calendar day.
   - For an absolute clock time, use the next occurrence of that IST time.
   The result MUST be strictly in the future relative to Current Time.

3. If no specific or relative time is mentioned (just "busy" or "later"), return null.
   Do NOT default to 24 hours.

**Email Extraction:**

1. Extract and return only the email address explicitly confirmed/corrected by the user during the call; ignore earlier unconfirmed email addresses extract last email that the user confirmed/corrected in conversation.
2. Return null if no email address is explicitly confirmed by the user.

**Confidence Classification:**

Analyze the customer's confidence/assertiveness using ONLY vocal/acoustic
characteristics, including:

- Vocal stability
- Pitch variation
- Energy
- Speaking pace
- Hesitation
- Pauses

Do NOT use the meaning of the customer's words to determine confidence.

Choose EXACTLY one:

- "low"
- "medium"
- "high"

**Background Noise Classification:**

Analyze the audio signal and determine the level of background noise.

Choose EXACTLY one:

- "none"
- "low"
- "medium"
- "high"

**Emotional Tone Classification:**

Determine the customer's primary emotional tone using ONLY vocal/acoustic
characteristics such as tone, pitch, energy, speaking pace, pauses,
hesitation, and vocal intensity.

Do NOT determine emotional tone from the meaning of the customer's words.

Choose EXACTLY one:

- "Neutral"
- "Positive"
- "Interested"
- "Excited"
- "Hesitant"
- "Frustrated"
- "Angry"
- "Disappointed"

**Next Recommended Action:**

Determine the most appropriate next action based on the conversation.

Choose EXACTLY one:

- "follow_up"
- "call_back"
- "send_product_information"
- "send_pricing"
- "send_proposal"
- "schedule_demo"
- "schedule_site_visit"
- "negotiate"
- "wait_for_decision"
- "close_lead"
- "no_action"
- "unknown"

**Overall Explanation:**

Provide one concise explanation covering the lead status, recommended action,
follow-up information, sales stage, confidence, background noise, and
emotional tone.

Only include information supported by the audio and conversation.

**Input Audio:**

Analyze the provided audio recording directly.

**Output Format:**

Return ONLY a JSON object (no markdown formatting) with the following structure:

{{

  "next_recommended_action": "follow_up" | "call_back" | "send_product_information" | "send_pricing" | "send_proposal" | "schedule_demo" | "schedule_site_visit" | "negotiate" | "wait_for_decision" | "close_lead" | "no_action" | "unknown",

  "follow_up_date_time": "ISO_STRING" or null,

  "sales_stage": "one of the sales stages listed above",

  "follow_up_required": true | false,

  "reason": "Brief explanation of why this lead status and sales stage were chosen.",

  "scheduled_time": "ISO_STRING" or null,

  "email": "extracted_email@example.com" or null,

  "title": "Suggested meeting title (e.g. 'Demo with Lead Name') or null",

  "confidence": "low" | "medium" | "high",

  "background_noise": "none" | "low" | "medium" | "high",

  "emotional_tone": "Neutral" | "Positive" | "Interested" | "Excited" | "Hesitant" | "Frustrated" | "Angry" | "Disappointed",

  "explanation": "Concise overall explanation covering all aspects of the analysis."
}}

**Important Output Rules:**

- Return ONLY the raw JSON object.
- Do not return markdown.
- Do not return code fences.
- Do not return additional text.
- The response must be directly parseable by json.loads().
- Do not invent information.
- Use null when information cannot reasonably be determined.
"""



GEMINI_MODEL="gemini-3.1-flash-lite"
# Initialize MongoDB (Separate connection to avoid circular imports)
MONGO_URI = os.getenv("MONGO_URI")
try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client.get_database()
    calls_collection = db["calls"]
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")


def validate_output(response):
    try:
        result = json.loads(response)
        SentimentResult.model_validate(result)
        return True
    except (json.JSONDecodeError, ValidationError):
        return False

async def _get_sentiments(recording_url,call_uuid):

    call_doc = calls_collection.find_one({"call_uuid": call_uuid})

    # Anchor relative callback times ("after 15 minutes") to when the call actually
    # ended, in IST. Injected per-call so it is never the stale server-start time.
    reference_time_ist = _resolve_reference_time_ist(call_doc)
    sentiment_prompt = GENERATE_SENTIMENTS.replace(
        REF_TIME_PLACEHOLDER, reference_time_ist.isoformat()
    )
    logger.info(f"[Sentiment] Reference time (IST) for {call_uuid}: {reference_time_ist.isoformat()}")

    # Download recording into memory
    async with aiohttp.ClientSession() as session:
        async with session.get(recording_url, ssl=False) as response:
            response.raise_for_status()
            audio_bytes = await response.read()

    if not audio_bytes:
        raise ValueError("Downloaded audio is empty")

    size_bytes = len(audio_bytes)
    size_mb = size_bytes / (1024 * 1024)
    print(f"Audio Size: {size_bytes} bytes ({size_mb:.2f} MB)")

    print("Content-Type:", response.headers.get("Content-Type"))
    print("First 20 bytes:", audio_bytes[:20])
    # Calculate duration — detect format from file header, not provider field
    is_wav = audio_bytes[:4] == b'RIFF'
    if is_wav:
        audio = WAVE(BytesIO(audio_bytes))
        mime_type = "audio/wav"
    else:
        audio = MP3(BytesIO(audio_bytes))
        mime_type = "audio/mpeg"
    duration_seconds = audio.info.length
    duration_minutes = duration_seconds / 60

    logger.info(
        f"Audio duration: {duration_seconds:.2f} seconds "
        f"({duration_minutes:.2f} minutes)"
    )

    observability = Observability()

    start_time = time.perf_counter()

    retries = 3
    try:
        for i in range(retries):
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=GEMINI_MODEL,
                contents=[
                    sentiment_prompt,
                    types.Part.from_bytes(
                        data=audio_bytes,
                        mime_type=mime_type,
                    ),
                ],
            )
            logger.info(f"Trying {i+1} time")
            if validate_output(response.text):
                break;
    except Exception as e:
        logger.error(f"Failed to get validated output after {retries}: {e}")

    logger.info(f"Usage metadata: {response.usage_metadata}")
    observability.record(response.usage_metadata)
    await observability.calculate_cost(GEMINI_MODEL)

    logger.info("Sentiment Observability metrics:%s",observability.metrics())


    total_cost = observability.total_cost_usd

    latency_seconds = time.perf_counter() - start_time
    latency_ms = latency_seconds * 1000

    cost_per_minute = (
        total_cost / duration_minutes
        if duration_minutes
        else 0
    )

    sentiments_metrics = {
        "model":GEMINI_MODEL,
        "total_cost_usd": round(total_cost, 3),
        "cost_per_minute_usd": round(cost_per_minute, 3),
        "observability": observability.metrics(),
    }

    redis_client.setex(
        f"sentiment_analysis_metrics:{call_uuid}",
        3600,
        json.dumps(sentiments_metrics)
    )
    print("Response",response.text)
    result = json.loads(response.text)
    # print(result)
    return result



if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("Usage: python -m sentiment.sentiment <audio_path>")

    audio_path = sys.argv[1]
    result = asyncio.run(_get_sentiments(audio_path))
    print(result)