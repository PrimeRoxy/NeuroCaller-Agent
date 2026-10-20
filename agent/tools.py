"""
ADK Tools for NeuroCaller Agent

PHILOSOPHY: The LLM handles most tasks conversationally.
Python tools are ONLY for actual API calls that require code.

Tools:
- initiate_outbound_call: Makes actual Plivo API calls
"""

from services.call_service_plivo import resume_campaign_service
import logging
from typing import List, Dict, Any, Optional, Union
import json
# Import call services for all providers
from services.config_service import ConfigService
from services.call_service_plivo import outbound_call as plivo_outbound_call
from services.call_service_voicelink import outbound_call as voicelink_outbound_call

# Hardcoded provider numbers — add more numbers here as needed
VOICELINK_NUMBERS = {"+917969460176"}  # Indian numbers → VoiceLink
from services.campaign_tracking_service import get_campaign_tracking_service, CampaignStatus
from services.salesforce_service import fetch_leads as fetch_sf_leads_service
# Configure logging
from services.external_calendar_service import trigger_schedule_agent

# Configure logging
logger = logging.getLogger("SalesmateTools")

from google.adk.tools import ToolContext


def _resolve_identity(
    tool_context: Optional[ToolContext],
    org_arg: Optional[str] = None,
    user_arg: Optional[str] = None,
    session_arg: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve (org_id, user_id, session_id), preferring the session state seeded
    from the A2A caller identity over anything the LLM passed as an argument.

    The A2A executor writes the caller's real ids into ``session.state``; reading
    them here makes identity deterministic and immune to the model hallucinating
    placeholder ids. Falls back to the passed args for the REST path and direct
    (non-tool) calls where there is no ToolContext / seeded state.
    """
    state = getattr(tool_context, "state", None) or {}
    org = state.get("organisation_id") or state.get("org_id") or org_arg
    user = state.get("user_id") or user_arg
    session = state.get("session_id") or session_arg
    return org, user, session

# ============================================================================
# OUTBOUND CALL TOOL - The only essential tool
# ============================================================================

async def initiate_outbound_call(
    phone_numbers: List[Any], 
    agent_number: str,
    welcome_message: Optional[str] = None,
    # agent_name: Optional[str] = None,
    # company_name: Optional[str] = None,
    prompt_name: Optional[str] = None,
    organisation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tool_context: ToolContext = None,
) -> Dict[str, Any]:
    """
    Initiates outbound phone calls to a list of lead phone numbers.
    
    This tool should be called when:
    - User has provided lead phone numbers (TO numbers) and optionally names
    - User has provided the Plivo number to call FROM
    - User has confirmed they want to start calling
    
    The LLM should:
    - Extract lead phone numbers and names from user input or files
    - Ask user which Plivo number to use for calling
    - Ensure all numbers are in E.164 format (+[country][number])
    - Remove duplicates before calling this tool
    - Confirm with user before calling
    - Pass the resolved welcome_message if available.

    Args:
        phone_numbers: List of lead phone numbers to call (TO numbers). 
                       Can be simple strings or dicts like {"number": "+123...", "name": "John"}.
        plivo_number: The Plivo phone number to call FROM.
        welcome_message: The opening line for the agent.
        prompt_name: Name of the generated system prompt to use.
        organisation_id: The organization ID for tracking and configuration.
        user_id: The user ID who is initiating the calls.

    Returns:
        A dictionary confirming the queue status and number of calls initiated.
    """
    organisation_id, user_id, _ = _resolve_identity(tool_context, organisation_id, user_id)
    logger.info(f"=== initiate_outbound_call CALLED ===")
    logger.info(f"Lead numbers (TO): {len(phone_numbers)} leads")
    logger.info(f"Plivo number (FROM): {agent_number}")
    logger.info(f"Org: {organisation_id}, User: {user_id}")
    logger.info(f"Welcome Message: {welcome_message}")

    # Check Quiet Hours
    if organisation_id:
        is_quiet, reason = ConfigService.is_quiet_hours(organisation_id)
        if is_quiet:
            logger.warning(f"Blocked campaign initiate due to Quiet Hours: {reason}")
            return {
                "status": "error",
                "message": f"Cannot start campaign: {reason}",
                "queued_count": 0
            }
    
    # Validate inputs
    if not phone_numbers:
        logger.warning("No phone numbers provided")
        return {
            "status": "error",
            "message": "No lead phone numbers provided. Please provide at least one phone number to call.",
            "queued_count": 0
        }
    
    if not agent_number:
        logger.warning("No Agent number provided")
        return {
            "status": "error",
            "message": "No Agent number provided. Please specify which Plivo number to use for making calls.",
            "queued_count": 0
        }
    
    # Process list to ensure consistent format for service
    leads_payload = []
    for item in phone_numbers:
        if isinstance(item, dict):
            leads_payload.append(item)
        else:
            leads_payload.append({"number": item, "name": ""})
            
    # Serialize to JSON string for the service which expects a string argument for now (or update service)
    # We will pass the list of dicts directly if we update the service, but let's send JSON string to be safe 
    # if the service signature is strictly typed in some frameworks, but here it's Python function call.
    # However, let's look at call_service.py. outbound_call expects "numbers: str".
    # We will change outbound_call signature in the next step.
    # For now, let's pass the list of dicts directly.
    
    try:
        # Route to the correct provider based on the selected FROM number
        clean_num = agent_number.strip()
        if clean_num in VOICELINK_NUMBERS or clean_num.startswith("+91") or "917969460176" in clean_num:
            logger.info(f"[Provider] Using VOICELINK for number {agent_number}")
            result = voicelink_outbound_call(
                leads=leads_payload, 
                organisation_id=organisation_id, 
                user_id=user_id,
                voicelink_number=agent_number,
                prompt_name=prompt_name,
                welcome_message=welcome_message,
            )
        else:
            logger.info(f"[Provider] Using PLIVO for number {agent_number}")
            result = plivo_outbound_call(
                leads=leads_payload, 
                organisation_id=organisation_id, 
                user_id=user_id,
                plivo_number=agent_number,
                prompt_name=prompt_name,
                welcome_message=welcome_message,
            )
        
        logger.info(f"outbound_call_service result: {result}")
        logger.info(f"=== initiate_outbound_call SUCCESS ===")
        
        return {
            "status": "success",
            "message": f"Successfully queued {len(phone_numbers)} lead numbers for outbound calls from {agent_number}.",
            "queued_count": len(phone_numbers),
            "agent_number": agent_number,
            "organisation_id": organisation_id,
            "user_id": user_id
        }
        
    except Exception as e:
        logger.error(f"initiate_outbound_call ERROR: {e}")
        return {
            "status": "error",
            "message": f"Failed to initiate calls: {str(e)}",
            "queued_count": 0
        }


# ============================================================================
# CAMPAIGN STATUS TOOL - Check progress
# ============================================================================

async def check_campaign_status(
    organisation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    status: Optional[str] = None,
    tool_context: ToolContext = None,
) -> Dict[str, Any]:
    """
    Check the status and progress of sales campaigns.
    
    Use this tool when the user asks questions like:
    - "How is my campaign going?"
    - "Is the calling finished?"
    - "Show me active campaigns"
    - "What's the status of my calls?"
    - "Show latest campaign"

    Args:
        organisation_id: The organization ID (context).
        user_id: The user ID (context).
        status: Optional filter. Default is None which returns ALL recent campaigns 
                regardless of status (recommended for first check).
                Only pass a value if the user explicitly requests a specific filter:
                - 'running' — ONLY if user says "show running campaigns"
                - 'completed' — ONLY if user says "show completed campaigns"
                - 'failed' — ONLY if user says "show failed campaigns"
                Do NOT default to 'running'. Leave as None to show the latest campaigns.
    
    Returns:
        A formatted summary of recent campaigns and their progress.
    """
    organisation_id, user_id, _ = _resolve_identity(tool_context, organisation_id, user_id)
    logger.info(f"=== check_campaign_status CALLED ===")
    logger.info(f"Org: {organisation_id}, User: {user_id}, Status: {status}")
    
    try:
        tracking = get_campaign_tracking_service()
        
        # If status is 'active', map to 'running'
        if status and status.lower() in ["active", "current"]:
            status = CampaignStatus.RUNNING
            
        # List campaigns (default to last 5 for relevance)
        campaigns = tracking.list_campaigns(
            org_id=organisation_id, 
            user_id=user_id, 
            status=status, 
            limit=5
        )
        
        if not campaigns:
            return {
                "status": "success",
                "message": "No campaigns found matching your criteria.",
                "campaigns": []
            }
            
        # Format for LLM consumption
        summary_list = []
        for c in campaigns:
            cb_scheduled = c.get("callbacks_scheduled", 0)
            cb_completed = c.get("callbacks_completed", 0)
            cb_pending = max(0, cb_scheduled - cb_completed)
            
            completed_calls = c.get("completed", 0)
            total_calls = c.get("total_numbers", 0)
            
            progress_str = f"{completed_calls}/{total_calls} calls completed"
            if cb_pending > 0:
                progress_str += f" ({cb_pending} callback requested)"
            elif cb_completed > 0:
                progress_str += f" ({cb_completed} callback completed)"
                
            summary_list.append({
                "id": c.get("campaign_id"),
                "status": c.get("status"),
                "progress": progress_str,
                "completed": completed_calls,
                "total": total_calls,
                "callbacks_scheduled": cb_scheduled,
                "callbacks_completed": cb_completed,
                "callbacks_pending": cb_pending,
                "failed": c.get("failed", 0),
                "in_progress": c.get("in_progress", 0),
                "started_at": c.get("started_at", "N/A")
            })
            
        logger.info(f"Found {len(summary_list)} campaigns")
        return {
            "status": "success",
            "count": len(summary_list),
            "campaigns": summary_list
        }
        
    except Exception as e:
        logger.error(f"check_campaign_status ERROR: {e}")
        return {
            "status": "error",
            "message": f"Failed to fetch status: {str(e)}"
        }

async def pause_campaign_tool(
    campaign_id: str,
    organisation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tool_context: ToolContext = None,
) -> Dict[str, Any]:
    """
    Pause a currently running campaign.
    
    This stops new calls from being initiated. Calls currently in progress will finish normally.
    
    Args:
        organisation_id: Organization ID
        user_id: User ID
        campaign_id: The ID of the campaign to pause
    
    Returns:
        Status dict
    """
    organisation_id, user_id, _ = _resolve_identity(tool_context, organisation_id, user_id)
    logger.info(f"Pausing campaign {campaign_id}")
    try:
        tracking = get_campaign_tracking_service()
        success = tracking.update_status(campaign_id, CampaignStatus.PAUSED)
        
        if success:
            return {"status": "success", "message": f"Campaign {campaign_id} paused. Ongoing calls will finish, but no new calls will start."}
        else:
             return {"status": "error", "message": f"Campaign {campaign_id} not found or could not be updated."}
    except Exception as e:
        logger.error(f"Pause error: {e}")
        return {"status": "error", "message": str(e)}

async def resume_campaign_tool(
    campaign_id: str,
    organisation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tool_context: ToolContext = None,
) -> Dict[str, Any]:
    """
    Resume a paused campaign.
    
    Restart the calling queue for a paused campaign.
    
    Args:
        organisation_id: Organization ID
        user_id: User ID
        campaign_id: The ID of the campaign to resume
    
    Returns:
        Status dict
    """
    organisation_id, user_id, _ = _resolve_identity(tool_context, organisation_id, user_id)
    logger.info(f"Resuming campaign {campaign_id}")
    try:
        # Use the service logic which handles queue restarting
        result = resume_campaign_service(campaign_id)
        return result
    except Exception as e:
        logger.error(f"Resume error: {e}")
        return {"status": "error", "message": str(e)}

async def cancel_campaign_tool(
    campaign_id: str,
    organisation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tool_context: ToolContext = None,
) -> Dict[str, Any]:
    """
    Cancel a active or paused campaign.
    
    This permanently stops the campaign.
    
    Args:
        organisation_id: Organization ID
        user_id: User ID
        campaign_id: The ID of the campaign to cancel
    
    Returns:
        Status dict
    """
    organisation_id, user_id, _ = _resolve_identity(tool_context, organisation_id, user_id)
    logger.info(f"Cancelling campaign {campaign_id}")
    try:
        tracking = get_campaign_tracking_service()
        success = tracking.update_status(campaign_id, CampaignStatus.CANCELLED)
        
        # Note: We are not clearing the Redis queue explicitly here, but since the
        # hangup loop checks for "running" status, it will stop processing anyway.
        # Ideally we should clear the queue, but status check is sufficient for stopping.
        
        if success:
            return {"status": "success", "message": f"Campaign {campaign_id} cancelled. No further calls will be made."}
        else:
             return {"status": "error", "message": f"Campaign {campaign_id} not found or could not be updated."}
    except Exception as e:
        logger.error(f"Cancel error: {e}")
        return {"status": "error", "message": str(e)}

async def check_quiet_hours_tool(
    organisation_id: Optional[str] = None,
    tool_context: ToolContext = None,
) -> Dict[str, Any]:
    """
    Check if the current time is within the configured Quiet Hours.

    Use this BEFORE starting any new campaign workflow.

    Returns:
         dict: {"status": "allowed" | "blocked", "message": "..."}
    """
    organisation_id, _, _ = _resolve_identity(tool_context, organisation_id)
    logger.info(f"[Tool] Checking quiet hours for org {organisation_id}")
    try:
        is_quiet, reason = ConfigService.is_quiet_hours(organisation_id)
        if is_quiet:
            return {"status": "blocked", "message": reason}
        else:
            return {"status": "allowed", "message": "Current time is within allowed operating hours."}
    except Exception as e:
        logger.error(f"check_quiet_hours_tool error: {e}")
        # Fail open (allow) if check fails, or fail closed? 
        # Safest is to allow but log error, or fail closed. Let's return error message.
        return {"status": "error", "message": str(e)}


async def get_org_defaults(
    organisation_id: Optional[str] = None,
    tool_context: ToolContext = None,
) -> Dict[str, Any]:
    """
    Fetch the organisation's configuration defaults and currently-free agent
    phone numbers.

    Call this at the START of a new campaign workflow to populate Agent Name,
    Company Name, Welcome Message, Voice, Gender, and the list of available
    outbound numbers. This is REQUIRED when the caller (e.g. an upstream A2A
    agent) has not already injected a [Current Configuration Defaults] block
    into the message, since without it those defaults are unknown.

    Args:
        organisation_id: The organization ID.

    Returns:
        dict: {
            "status": "ok" | "error",
            "agent_name": str, "company_name": str, "welcome_message": str,
            "voice_model": str, "agent_gender": str,
            "available_numbers": List[str],
            "message": str  # present only on error
        }
    """
    organisation_id, _, _ = _resolve_identity(tool_context, organisation_id)
    logger.info(f"[Tool] Fetching org defaults for org {organisation_id}")
    config = ConfigService.find_config(organisation_id) or {}
    if not config:
        logger.warning(f"get_org_defaults: no configuration found for org {organisation_id}")

    try:
        available_numbers = ConfigService.get_available_phone_numbers(organisation_id)
    except Exception as e:
        logger.warning(f"get_org_defaults: available-numbers lookup failed: {e}")
        available_numbers = []

    return {
        "status": "ok",
        "agent_name": config.get("allyroid_name", "Not Set"),
        "company_name": config.get("organisation_name", "Not Set"),
        "welcome_message": config.get("script", "Not Set"),
        "voice_model": config.get("allyroid_voice", "ash"),
        "agent_gender": config.get("allyroid_gender", "Neutral"),
        "available_numbers": available_numbers,
    }

# ============================================================================
# AUTHENTICATION TOOL
# ============================================================================

async def authenticate_user_tool(
    prompt: str = "I want to authenticate my email.",
    organisation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tool_context: ToolContext = None,
) -> Dict[str, Any]:

    organisation_id, user_id, session_id = _resolve_identity(
        tool_context, organisation_id, user_id, session_id
    )
    logger.info(f"=== authenticate_user_tool CALLED ===")
    logger.info(f"Org: {organisation_id}, User: {user_id}, Session: {session_id}")
    logger.info(f"Prompt: {prompt}")
    
    result = await trigger_schedule_agent(
        organisation_id=organisation_id,
        user_id=user_id,
        session_id=session_id,
        prompt=prompt
    )
    
    if result.get("status") == "success":
        logger.info("External agent request successful")
        return {
            "status": "success",
            "message": result.get("message", "Request processed."),
            "raw_response": result.get("raw_response")
        }
    else:
        logger.error(f"authenticate_user_tool ERROR: {result.get('message')}")
        return result

async def fetch_salesforce_leads_tool(
    limit: int = 200,
    organisation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tool_context: ToolContext = None,
) -> Dict[str, Any]:
    """
    Fetches lead data (Names and Phone Numbers) directly from Salesforce.
    
    Use this tool when the user says:
    - "Call my Salesforce leads"
    - "Pull leads from CRM"
    - "Use leads from Salesforce"
    - "Get my leads from SF"
    
    This avoids the user having to manually provide lists of names and numbers.
    
    Args:
        organisation_id: The organization ID.
        user_id: The user ID.
        limit: Maximum number of leads to fetch (default 50).
        
    Returns:
        A list of leads with name and phone number.
    """
    organisation_id, user_id, _ = _resolve_identity(tool_context, organisation_id, user_id)
    logger.info(f"=== fetch_salesforce_leads_tool CALLED ===")
    logger.info(f"Org: {organisation_id}, User: {user_id}")
    
    try:
        config = ConfigService.find_config(organisation_id)
        if not config:
            return {
                "status": "error",
                "message": "Organisation configuration not found. Please set up your configuration first."
            }
        sf_creds = config.get("salesforce_creds", {})
        
        client_id = sf_creds.get("client_id")
        client_secret = sf_creds.get("client_secret")
        domain = sf_creds.get("domain")
        
        if not all([client_id, client_secret]):
            return {
                "status": "error",
                "message": "Salesforce is not fully configured. Please provide client_id and client_secret in settings."
            }
            
        leads = fetch_sf_leads_service(
            client_id=client_id,
            client_secret=client_secret,
            domain=domain,
            limit=limit
        )
        
        return {
            "status": "success",
            "count": len(leads),
            "leads": leads
        }
    except Exception as e:
        logger.error(f"fetch_salesforce_leads_tool ERROR: {e}")
        return {
            "status": "error",
            "message": f"Failed to fetch leads: {str(e)}"
        }

# ============================================================================
# EXPORT
# ============================================================================

# Alias for backward compatibility
outbound_call_tool = initiate_outbound_call