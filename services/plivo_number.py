import os
from dotenv import load_dotenv
from typing import List, Dict
import plivo
from plivo.exceptions import PlivoRestError
from call.plivo import orgcalls_collection
load_dotenv()

# Mapping of ISO country codes to country names
ISO_TO_COUNTRY = {
    "IN": "India",
    "UAE": "United Arab Emirates",
    "US": "United States"
}

# Initialize Plivo client
auth_id = os.getenv("PLIVO_AUTH_ID")
auth_token = os.getenv("PLIVO_AUTH_TOKEN")
client = plivo.RestClient(auth_id, auth_token)


def get_available_countries() -> List[Dict[str, str]]:
    """
    Returns a list of specific countries with their ISO codes.
    """
    return [
        {"name": "India", "code": "IN"},
        {"name": "United Arab Emirates", "code": "UAE"},
        {"name": "United States", "code": "US"}
    ]


def get_rented_numbers(country_code: str = None) -> List[str]:
    """
    Returns a list of phone numbers currently rented under the Plivo account.
    Optionally filters by country code.
    Also excludes numbers already assigned to any user or organization in MongoDB.
    """
    try:
        # 1. Fetch all rented numbers from Plivo
        response = client.numbers.list()
        print(f"Retrieved {len(response.objects)} numbers from Plivo")
        
        all_plivo_numbers = [f"+{num.number}" for num in response.objects]

        # 2. Filter by country code if given
        if country_code:
            country_name = ISO_TO_COUNTRY.get(country_code.upper())
            if country_name:
                all_plivo_numbers = [
                    f"+{num.number}"
                    for num in response.objects
                    if num.country == country_name
                ]

        # 3. Fetch assigned numbers from MongoDB
        assigned_docs = orgcalls_collection.find({}, {"_id": 0, "phone_number": 1})
        assigned_numbers = set()
        for doc in assigned_docs:
            # Extract phone_number directly
            pn = doc.get("phone_number", "").strip()
            if pn:
                assigned_numbers.add(pn)

        # 4. Filter out assigned numbers
        available_numbers = [num for num in all_plivo_numbers if num not in assigned_numbers]

        return available_numbers

    except PlivoRestError as e:
        raise e