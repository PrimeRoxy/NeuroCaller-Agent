import os
from dotenv import load_dotenv
from typing import List, Dict
import plivo
from plivo.exceptions import PlivoRestError
from services.call_service_voicelink import orgcalls_collection
from core.config import is_production
from services.call_service_voicelink import get_voicelink_numbers
import logging

load_dotenv()

# Mapping of ISO country codes to country names
ISO_TO_COUNTRY = {
    "IN": "India",
    "UAE": "United Arab Emirates",
    "US": "United States"
}

# Initialize Plivo client safely
auth_id = os.getenv("PLIVO_AUTH_ID")
auth_token = os.getenv("PLIVO_AUTH_TOKEN")
client = None
if auth_id and auth_token:
    try:
        client = plivo.RestClient(auth_id, auth_token)
    except Exception as e:
        print(f"Plivo client init warning: {e}")


def get_available_countries() -> List[Dict[str, str]]:
    """
    Returns a list of specific countries with their ISO codes.
    In production, only India is available as only VoiceLink is enabled.
    """
    if is_production():
        return [
            {"name": "India", "code": "IN"}
        ]
    return [
        {"name": "India", "code": "IN"},
        {"name": "United Arab Emirates", "code": "UAE"},
        {"name": "United States", "code": "US"}
    ]


def get_rented_numbers(country_code: str = None) -> List[str]:
    """
    Returns a list of phone numbers currently rented under the account.
    Optionally filters by country code:
    - US: returns US numbers (+1...)
    - UAE / AE: returns UAE numbers (+971..., e.g., FreJun)
    - IN / India: returns India numbers (+91..., e.g., VoiceLink)

    In Production:
    - ONLY VoiceLink numbers (India +91) are enabled/available.
    - Test number (VOICELINK_NUMBER) is excluded.
    - Numbers already assigned to any organisation in MongoDB config are excluded.

    In Non-Production (Dev / Local / Staging):
    - All services (VoiceLink, Plivo, FreJun, Twilio) are available.
    """
    # -------------------------------------------------------------------------
    # PRODUCTION MODE: Only VoiceLink is available (numbers from .env)
    # -------------------------------------------------------------------------
    if is_production():
        logging.info("[RentedNumbers] Production mode: only VoiceLink numbers from .env are enabled")

        # If a non-India country is requested in production, return empty
        if country_code and country_code.strip().upper() not in ["IN", "INDIA"]:
            return []

        # Read VoiceLink production numbers from .env
        env_raw = os.getenv(
            "VOICELINK_PRODUCTION_NUMBERS",
            "919484958621,919484958622,919484955294,919484955296"
        )
        voicelink_numbers = []
        raw_list = env_raw.replace("\n", ",").replace(";", ",").replace('"', "").replace("'", "").split(",")
        for num in raw_list:
            num_str = str(num).strip()
            if not num_str:
                continue
            if not num_str.startswith("+"):
                if num_str.startswith("91"):
                    num_str = "+" + num_str
                elif len(num_str) == 10 and num_str.isdigit():
                    num_str = "+91" + num_str
                else:
                    num_str = "+" + num_str
            if num_str not in voicelink_numbers:
                voicelink_numbers.append(num_str)

        available_numbers = list(voicelink_numbers)

        # Exclude numbers already assigned to ANY organisation in MongoDB
        try:
            occupied_numbers = set()
            docs = orgcalls_collection.find({}, {"phone_numbers": 1, "phone_number": 1})
            for doc in docs:
                for p in (doc.get("phone_numbers") or []):
                    if p:
                        s = str(p).strip()
                        occupied_numbers.add(s)
                        occupied_numbers.add(s.lstrip("+"))
                        occupied_numbers.add(f"+{s.lstrip('+')}")
                single_p = doc.get("phone_number")
                if single_p:
                    s = str(single_p).strip()
                    occupied_numbers.add(s)
                    occupied_numbers.add(s.lstrip("+"))
                    occupied_numbers.add(f"+{s.lstrip('+')}")

            if occupied_numbers:
                before_count = len(available_numbers)
                available_numbers = [
                    num for num in available_numbers
                    if num not in occupied_numbers
                ]
                logging.info(f"[RentedNumbers] Production: filtered {before_count - len(available_numbers)} occupied numbers")
        except Exception as e:
            logging.error(f"[RentedNumbers] Error filtering occupied numbers from MongoDB: {e}")

        return available_numbers

    # -------------------------------------------------------------------------
    # NON-PRODUCTION MODE (Dev / Staging / Local): All services available
    # -------------------------------------------------------------------------
    all_plivo_numbers = []
    if client:
        try:
            response = client.numbers.list()
            all_plivo_numbers = [f"+{num.number}" for num in response.objects]
        except Exception as e:
            print(f"Retrieved Plivo numbers warning: {e}")



    voicelink_numbers = []

    static_plivo = os.getenv("PLIVO_NUMBER", "+18333831850").strip()
    static_twilio = os.getenv("TWILIO_PHONE_NUMBER", "+18382064847").strip()
    static_frejun = os.getenv("FREJUN_FROM_NUMBER", "+97148987384").strip()
    static_voicelink = str(os.getenv("VOICELINK_NUMBER", "917969460176")).strip()
    if not static_voicelink.startswith("+"):
        static_voicelink = "+" + static_voicelink

    available_numbers = []

    if country_code:
        norm_code = country_code.strip().upper()
        if norm_code in ["UAE"]:
            available_numbers = [num for num in all_plivo_numbers if num.startswith("+971")]
            if static_frejun not in available_numbers:
                available_numbers.append(static_frejun)
        elif norm_code in ["US"]:
            available_numbers = [num for num in all_plivo_numbers if num.startswith("+1")]
            if static_plivo not in available_numbers:
                available_numbers.append(static_plivo)
            if static_twilio not in available_numbers:
                available_numbers.append(static_twilio)
        elif norm_code in ["IN"]:
            available_numbers = [num for num in all_plivo_numbers if num.startswith("+91")]
            for vl_num in voicelink_numbers:
                if vl_num not in available_numbers:
                    available_numbers.append(vl_num)

            if static_voicelink not in available_numbers:
                available_numbers.append(static_voicelink)
        else:
            available_numbers = list(all_plivo_numbers)
            for vl_num in voicelink_numbers:
                if vl_num not in available_numbers:
                    available_numbers.append(vl_num)

    else:
        available_numbers = list(all_plivo_numbers)
        for vl_num in voicelink_numbers:
            if vl_num not in available_numbers:
                available_numbers.append(vl_num)

        if static_plivo not in available_numbers:
            available_numbers.append(static_plivo)
        if static_twilio not in available_numbers:
            available_numbers.append(static_twilio)
        if static_frejun not in available_numbers:
            available_numbers.append(static_frejun)
        if static_voicelink not in available_numbers:
            available_numbers.append(static_voicelink)

    logging.info("[RentedNumbers] Non-production mode: returning all available numbers")
    return available_numbers