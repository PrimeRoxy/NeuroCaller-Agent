"""
Quick Call API — Simple one-number call initiation using VoiceLink + Gemini Live pipeline.

Usage:
    POST /quick-call?phone_number=+919876543210
    POST /quick-call  (JSON body: {"phone_number": "+919876543210"})

All parameters (welcome message, system prompt, voice, caller ID, etc.)
use sensible defaults so the caller only needs to supply a phone number.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.call_service_voicelink import (
    execute_outbound_call,
    get_campaign_id,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Quick Call"])


# ─────────────────────────────────────────────────────────────────────────────
# Defaults — override via environment variables
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_VOICE = os.getenv("QUICK_CALL_VOICE", "Autonoe")
DEFAULT_WELCOME_MESSAGE = os.getenv(
    "QUICK_CALL_WELCOME",
    "नमस्ते, मैं Sakshi बोल रही हूँ Em Pee Motors से। हम Toyota और Lexus vehicles deal करते हैं। मैं यह जानने के लिए call कर रही हूँ कि क्या आप अभी नई car लेने की planning कर रहे हैं, या अपनी existing vehicle upgrade करना चाहते हैं, या बस कुछ नए vehicle options explore करना चाहते हैं?"
)
DEFAULT_ORG_ID = os.getenv("QUICK_CALL_ORG_ID", "manual")
DEFAULT_USER_ID = os.getenv("QUICK_CALL_USER_ID", "api_user")


# ─────────────────────────────────────────────────────────────────────────────
# Request model
# ─────────────────────────────────────────────────────────────────────────────

class QuickCallRequest(BaseModel):
    phone_number: str = Field(..., description="Phone number to call (e.g. +919876543210 or 9876543210)")
    lead_name: Optional[str] = Field("", description="Optional lead name")
    welcome_message: Optional[str] = Field(None, description="Override default welcome message")
    voice: Optional[str] = Field(None, description="Override default voice (e.g. Charon, Puck, etc.)")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/quick-call")
async def quick_call(
    request: Optional[QuickCallRequest] = None,
    phone_number: Optional[str] = Query(None, description="Phone number (query param fallback)"),
):
    """
    Initiate an outbound call with a single phone number via VoiceLink.

    Everything else — welcome message, system prompt, voice, caller ID,
    organisation, and user — uses defaults. Supply overrides in the JSON
    body if needed.
    """
    # Resolve phone number from body or query param
    number = None
    lead_name = ""
    welcome = DEFAULT_WELCOME_MESSAGE
    voice = DEFAULT_VOICE

    if request:
        number = request.phone_number
        lead_name = request.lead_name or ""
        welcome = request.welcome_message or DEFAULT_WELCOME_MESSAGE
        voice = request.voice or DEFAULT_VOICE
    elif phone_number:
        number = phone_number
    else:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "phone_number is required."},
        )

    # Normalize: strip spaces, keep digits
    clean = number.strip().replace(" ", "")
    if clean.startswith("+"):
        clean = clean[1:]  # Remove leading +
    # If 10 digits, assume India — prepend 91
    digits_only = "".join(c for c in clean if c.isdigit())
    if len(digits_only) == 10:
        digits_only = "91" + digits_only

    if not digits_only:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Invalid phone number."},
        )

    # Generate a campaign_id so the call shows up in the live dashboard
    campaign_id = get_campaign_id(DEFAULT_ORG_ID, DEFAULT_USER_ID)

    logger.info(
        "[QuickCall] Initiating VoiceLink call to %s (lead=%s, voice=%s)",
        digits_only, lead_name or "(none)", voice,
    )

    # Use VoiceLink execute_outbound_call which:
    # 1. Creates a call_uuid
    # 2. Calls VoiceLink add-lead API
    # 3. Stores config in Redis (picked up by the Gemini Live WebSocket handler)
    success = execute_outbound_call(
        to_number=digits_only,
        agent_voicelink_number="",       # Uses VOICELINK_NUMBER from env
        welcome_message=welcome,
        system_prompt=None,              # Uses default from Gemini Live handler
        campaign_id=campaign_id,
        lead_name=lead_name,
        agent_voice=voice,
        agent_language=None,
        organisation_id=DEFAULT_ORG_ID,
        user_id=DEFAULT_USER_ID,
    )

    if success:
        return {
            "status": "success",
            "message": f"Call initiated to +{digits_only}",
            "phone_number": f"+{digits_only}",
        }
    else:
        return JSONResponse(
            status_code=500,
            content={
                "status": "failed",
                "message": f"Failed to initiate call to +{digits_only}. Check server logs.",
            },
        )
