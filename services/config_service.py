
import os
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from fastapi import HTTPException
from dotenv import load_dotenv
import pytz
from services.call_service_voicelink import orgcalls_collection
from services.campaign_tracking_service import get_campaign_tracking_service, CampaignStatus
load_dotenv()

import re

TIMEZONE_ALIASES = {
    "IST": "Asia/Kolkata",
    "UTC": "UTC",
    "GMT": "GMT",
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
}

def resolve_timezone(timezone_str: Optional[str]):
    """
    Resolve timezone string to a pytz timezone object, defaulting to Asia/Kolkata.
    Maps common abbreviations such as 'IST' to canonical IANA timezone 'Asia/Kolkata'.
    """
    if not timezone_str:
        return pytz.timezone("Asia/Kolkata")

    cleaned = str(timezone_str).strip()
    target = TIMEZONE_ALIASES.get(cleaned.upper(), cleaned)
    try:
        return pytz.timezone(target)
    except Exception as tz_err:
        logging.warning(
            f"[QuietHours] Invalid timezone '{timezone_str}': {tz_err}. Fallback to Asia/Kolkata."
        )
        return pytz.timezone("Asia/Kolkata")

def parse_time_string(t_str: str):
    """
    Parse a configured time into a `datetime.time`.

    Accepts both the 12h formats ("07:30 PM", "7:30pm", "10 : 00 pm")
    and plain 24h ("19:30"). Raises ValueError on anything else.
    """
    if not isinstance(t_str, str):
        raise ValueError(f"Unknown time format: {t_str}")

    t_str = t_str.strip().upper()
    # Normalize spaces around colons (e.g. "10 : 00 PM" -> "10:00 PM")
    t_str = re.sub(r"\s*:\s*", ":", t_str)
    # Normalize multiple whitespace characters
    t_str = re.sub(r"\s+", " ", t_str)

    # Fix single digit hour "7:00 PM" -> "07:00 PM" for strict %I parsing
    if len(t_str) > 2 and t_str[1] == ":":
        t_str = "0" + t_str

    formats = [
        "%I:%M %p",    # 07:30 PM
        "%I:%M%p",     # 07:30PM
        "%H:%M",       # 19:30
        "%I %p",       # 07 PM
        "%I%p",        # 7PM
        "%H:%M:%S",    # 19:30:00
        "%I:%M:%S %p", # 07:30:00 PM
    ]
    for fmt in formats:
        try:
            return datetime.strptime(t_str, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Unknown time format: {t_str}")


class ConfigService:
    
    @staticmethod
    async def upsert_config(
        user_id: str,
        organisation_id: str,
        allyroid_name: Optional[str] = None,
        organisation_name: Optional[str] = None,
        allyroid_voice: Optional[str] = None,
        allyroid_gender: Optional[str] = None,
        phone_numbers: Optional[List[str]] = None,
        crm_provider: Optional[str] = None,
        script: Optional[str] = None,
        timezone: Optional[str] = None,
        working_hours_start: Optional[str] = None,
        working_hours_end: Optional[str] = None,
        quiet_hours_start: Optional[str] = None,
        quiet_hours_end: Optional[str] = None,
        # Salesforce Credentials
        salesforce_client_id: Optional[str] = None,
        salesforce_client_secret: Optional[str] = None,
        salesforce_domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create or update configuration for an organisation and user.
        """
        
        # Check for existing configuration
        existing = orgcalls_collection.find_one({
            "organisation_id": organisation_id,
            "user_id": user_id
        })
        
        is_update = existing is not None
        
        # ---- Prepare MongoDB Data ----
        data = {
            "user_id": user_id,
            "organisation_id": organisation_id,
            "updated_at": datetime.utcnow()
        }
        
        if allyroid_name is not None: data["allyroid_name"] = allyroid_name
        if organisation_name is not None: data["organisation_name"] = organisation_name
        if allyroid_voice is not None: data["allyroid_voice"] = allyroid_voice
        if allyroid_gender is not None: data["allyroid_gender"] = allyroid_gender
        if phone_numbers is not None: data["phone_numbers"] = phone_numbers
        if crm_provider is not None: data["crm_provider"] = crm_provider
        if script is not None: data["script"] = script
        if timezone is not None: data["timezone"] = timezone
        if working_hours_start is not None: data["working_hours_start"] = working_hours_start
        if working_hours_end is not None: data["working_hours_end"] = working_hours_end
        if quiet_hours_start is not None: data["quiet_hours_start"] = quiet_hours_start
        if quiet_hours_end is not None: data["quiet_hours_end"] = quiet_hours_end
        
        # Handle Salesforce Credentials
        if any([salesforce_client_id, salesforce_client_secret]):
            # Fetch existing creds if this is an update to merge/overwrite
            current_creds = existing.get("salesforce_creds", {}) if existing else {}
            
            new_creds = current_creds.copy()
            if salesforce_client_id: new_creds["client_id"] = salesforce_client_id
            if salesforce_client_secret: new_creds["client_secret"] = salesforce_client_secret
            if salesforce_domain: new_creds["domain"] = salesforce_domain
            
            # Remove old fields if they exist in current_creds
            new_creds.pop("username", None)
            new_creds.pop("password", None)
            new_creds.pop("security_token", None)
            
            data["salesforce_creds"] = new_creds
        
        # ---- DB Operation ----
        if is_update:
            result = orgcalls_collection.update_one(
                {"organisation_id": organisation_id, "user_id": user_id},
                {"$set": data}
            )
            action = "updated"
            doc_id = str(existing["_id"])
        else:
            data["created_at"] = datetime.utcnow()
            result = orgcalls_collection.insert_one(data)
            action = "created"
            doc_id = str(result.inserted_id)
            
        return {
            "message": f"Configuration {action} successfully",
            "id": doc_id,
            "action": action,
            "organisation_id": organisation_id,
            "user_id": user_id
        }

    @staticmethod
    def find_config(organisation_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return the config dict or None when not found (safe for non-HTTP callers)."""
        query = {"organisation_id": organisation_id}
        if user_id is not None:
            query["user_id"] = user_id
        return orgcalls_collection.find_one(query, {"_id": 0})

    @staticmethod
    def get_config(organisation_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        result = ConfigService.find_config(organisation_id, user_id)
        if not result:
            raise HTTPException(status_code=404, detail="Configuration not found.")
        return result

    @staticmethod
    def delete_config(user_id: str, organisation_id: str) -> Dict[str, Any]:
        config = orgcalls_collection.find_one({"user_id": user_id, "organisation_id": organisation_id})
        if not config:
            raise HTTPException(status_code=404, detail="Configuration not found.")
            
        orgcalls_collection.delete_one({"user_id": user_id, "organisation_id": organisation_id})
        return {"message": "Configuration deleted successfully."}

    @staticmethod
    def get_available_phone_numbers( organisation_id: str) -> List[str]:
        """
        Get list of phone numbers configured for the organisation that are NOT currently
        in use by any active campaign.
        
        NOTE: This looks for ANY config associated with the organisation_id, 
        and checks for ANY active campaign in the organisation, regardless of user.
        """
        # 1. Get all numbers from config (Organisation-wide)
        # We search for any config document for this org that has phone_numbers
        config = orgcalls_collection.find_one(
            {"organisation_id": organisation_id, "phone_numbers": {"$exists": True, "$ne": []}},
            {"phone_numbers": 1}
        )
        
        if not config:
           return []
           
        all_numbers = config.get("phone_numbers", [])
        if not all_numbers:
            return []
            
        # 2. Get numbers currently in use (Organisation-wide)
        tracking = get_campaign_tracking_service()
        
        active_statuses = [
            CampaignStatus.CREATED, 
            CampaignStatus.RUNNING
        ]
        busy_numbers = set()
        
        # Check active campaigns for the ENTIRE organization (user_id=None)
        for status in active_statuses:
            campaigns = tracking.list_campaigns(org_id=organisation_id, user_id=None, status=status)
            for campaign in campaigns:
                plivo_number = campaign.get("plivo_number")
                if plivo_number:
                    busy_numbers.add(plivo_number)
                    
        # 3. Filter available numbers
        available = [num for num in all_numbers if num not in busy_numbers]
        return all_numbers

    @staticmethod
    def is_quiet_hours(organisation_id: str) -> Tuple[bool, str]:
        """
        Check if the current time coincides with the configured quiet hours for the organization.
        Returns: (is_quiet, message)
        """
        # Find config with quiet hours for this org
        config = orgcalls_collection.find_one(
            {
                "organisation_id": organisation_id, 
                "quiet_hours_start": {"$exists": True, "$ne": None},
                "quiet_hours_end": {"$exists": True, "$ne": None}
            }
        )
        
        if not config:
            return False, ""
            
        start_str = config.get("quiet_hours_start")
        end_str = config.get("quiet_hours_end")
        timezone_str = config.get("timezone") # Get timezone from config
        
        if not start_str or not end_str:
            return False, ""
            
        try:
            # Parse times (Support both HH:MM 24h and HH:MM AM/PM 12h formats)
            # The frontend sends formats like "07:30 PM" or "7:00 pm"
            
            # Resolve timezone (defaults to Asia/Kolkata, maps aliases like IST)
            tz = resolve_timezone(timezone_str)
            now_localized = datetime.now(tz)
            now = now_localized.time()
            time_label = getattr(tz, "zone", str(tz))
            logging.info(f"[QuietHours] Using Timezone: {time_label}")
            
            start_time = parse_time_string(start_str)
            end_time = parse_time_string(end_str)
            
            logging.info(f"[QuietHours] Checking Org: {organisation_id}")
            logging.info(f"[QuietHours] Raw Config ({time_label}) -> Start: '{start_str}', End: '{end_str}'")
            logging.info(f"[QuietHours] Parsed (24h)  -> Start: {start_time}, End: {end_time}")
            logging.info(f"[QuietHours] Current Time ({time_label}): {now}")
            
            is_quiet = False
            
            if start_time < end_time:
                # Standard range (e.g., 22:00 to 23:00) 
                if start_time <= now <= end_time:
                    is_quiet = True
            else:
                # Overnight range (e.g., 22:00 to 06:00)
                if now >= start_time or now <= end_time:
                    is_quiet = True
            
            if is_quiet:
                logging.info(f"[QuietHours] Result: BLOCKED")
                msg = (
                    f"I apologize, but I am unable to initiate the call at this time. "
                    f"The operation is restricted because the current time ({now.strftime('%H:%M')} {time_label}) "
                    f"is within the configured Quiet Hours ({start_str} - {end_str}).\n\n"
                    f"Please try again during Working hours."
                )
                return True, msg
            
            logging.info(f"[QuietHours] Result: ALLOWED")
                
        except Exception as e:
            logging.error(f"Error checking quiet hours: {e}")
            # If we fail to parse, we should probably NOT block to be safe, or block safely.
            # Here we follow previous pattern: Log error but return False 
            return False, f"Error checking quiet hours: {e}"
            
        return False, ""

# Central source of truth (keep this updated manually)
AVAILABLE_VOICES = [
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar"
]