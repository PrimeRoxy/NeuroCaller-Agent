import os
import httpx
import logging
from typing import Dict, Any
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("ExternalCalendarService")
SCHEDULER_URL = os.getenv("SCHEDULER_URL")
AGENT_ID = os.getenv("SALES_AGENT_ID")

async def trigger_schedule_agent(
    organisation_id: str,
    user_id: str,
    session_id: str,
    prompt: str
) -> Dict[str, Any]:
    """
    Sends a request to the external scheduling agent API.
    
    Args:
        organisation_id: Organization ID
        user_id: User ID
        session_id: Session ID
        prompt: The instruction for the agent (e.g., "authenticate email" or "schedule meeting at X")
        
    Returns:
        JSON response from the agent API or error dict.
    """
    url = f"{SCHEDULER_URL}/api/v1/schedule/agent"
    
    payload = {
        "user_id": user_id,
        "prompt": prompt,
        "organization_id": organisation_id,
        "agent_id": AGENT_ID,
        "session_id": session_id
    }
    
    logger.info(f"[ExternalCalendar] Sending request. Prompt: '{prompt}', Org: {organisation_id}, User: {user_id}")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=20.0)
            response.raise_for_status()
            data = response.json()
            logger.info("[ExternalCalendar] Request successful.")
            return {
                "status": "success",
                # "message": data.get("message", "Request processed."),
                # "raw_response": data
                "message": "You are authenticated",
            }
    except Exception as e:
        logger.error(f"[ExternalCalendar] Request failed: {e}")
        return {
            "status": "error",
            "message": f"External agent request failed: {str(e)}"
        }
