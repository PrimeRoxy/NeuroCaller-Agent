import requests
import logging
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from core.config import get_redis_json, set_redis_json

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SF_VERSION = "v59.0"

# ----------------------------
# REQUIRED FIELDS
# ----------------------------
REQUIRED_LEAD_FIELDS = [
    {"name": "Agent_Number__c", "label": "Agent Number", "type": "Text", "length": 255},
    {"name": "Call_Attempts__c", "label": "Call Attempts", "type": "Number", "precision": 18, "scale": 0},
    {"name": "Duration_Seconds__c", "label": "Duration (Seconds)", "type": "Number", "precision": 18, "scale": 0},
    {"name": "Next_Best_Action__c", "label": "Next Best Action", "type": "Text", "length": 255},
    {"name": "Follow_Up_Required__c", "label": "Follow Up Required", "type": "Text", "length": 10},
    {"name": "Outcome__c", "label": "Call Outcome", "type": "Text", "length": 255},
    {"name": "Outcome_Reason__c", "label": "Outcome Reason", "type": "LongTextArea", "length": 32000, "visibleLines": 3},
]

# ----------------------------
# AUTH
# ----------------------------
def get_salesforce_token(client_id, client_secret, domain=None, force_refresh=False):
    if not client_id or not client_secret:
        logger.warning("[Salesforce] Missing client_id or client_secret. Bypassing Salesforce sync.")
        return None

    cache_key = f"sf_token:{client_id}"

    if not force_refresh:
        cached = get_redis_json(cache_key)
        if cached:
            logger.info("Using cached Salesforce token")
            return cached

    base = domain or "https://orgfarm-af1896b67b-dev-ed.develop.my.salesforce.com"
    if not base.startswith("http"):
        base = f"https://{base}"

    url = f"{base}/services/oauth2/token"

    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }

    try:
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"[Salesforce] Auth failed ({resp.status_code}): {resp.text[:200]}. Bypassing Salesforce sync.")
            return None

        token = resp.json()
        set_redis_json(cache_key, token, expire=7200)
        return token
    except Exception as e:
        logger.warning(f"[Salesforce] Auth request exception ({e}). Bypassing Salesforce sync.")
        return None

# ----------------------------
# HELPERS
# ----------------------------
def get_headers(token):
    return {
        "Authorization": f"Bearer {token['access_token']}",
        "Content-Type": "application/json"
    }

def get_lead_fields(instance_url, headers):
    url = f"{instance_url}/services/data/{SF_VERSION}/sobjects/Lead/describe"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return {f["name"] for f in resp.json()["fields"]}

def get_existing_custom_fields(instance_url, headers, object_name):
    query = f"""
    SELECT DeveloperName 
    FROM CustomField 
    WHERE TableEnumOrId = '{object_name}'
    """
    url = f"{instance_url}/services/data/{SF_VERSION}/tooling/query"
    resp = requests.get(url, headers=headers, params={"q": query})
    resp.raise_for_status()

    return {f["DeveloperName"] + "__c" for f in resp.json()["records"]}

def filter_valid_fields(data, allowed_fields):
    return {k: v for k, v in data.items() if k in allowed_fields}

# ----------------------------
# FIELD CREATION
# ----------------------------
def ensure_custom_fields_exist(client_id, client_secret, domain=None, object_name="Lead"):
    cache_key = f"sf_fields_ensured:{client_id}:{object_name}"

    if get_redis_json(cache_key):
        logger.info("Fields already ensured (cached)")
        return True

    try:
        token = get_salesforce_token(client_id, client_secret, domain)
        if not token:
            logger.warning("[Salesforce] No valid token available. Skipping field check.")
            return False
        headers = get_headers(token)
        instance_url = token["instance_url"]

        existing_fields = get_existing_custom_fields(instance_url, headers, object_name)
        print("Existing fields:",existing_fields)
        print("Length :",len(existing_fields))
        missing_fields = [
            f for f in REQUIRED_LEAD_FIELDS
            if f["name"] not in existing_fields
        ]

        if not missing_fields:
            logger.info("All fields already exist")
            set_redis_json(cache_key, True, expire=86400)
            return True

        tooling_url = f"{instance_url}/services/data/{SF_VERSION}/tooling/sobjects/CustomField"

        for field in missing_fields:
            metadata = {
                "FullName": f"{object_name}.{field['name']}",
                "Metadata": {
                    "label": field["label"],
                    "type": field["type"],
                    "required": False,
                    "externalId": False,
                }
            }

            if "length" in field:
                metadata["Metadata"]["length"] = field["length"]
            if "precision" in field:
                metadata["Metadata"]["precision"] = field["precision"]
                metadata["Metadata"]["scale"] = field.get("scale", 0)
            if "visibleLines" in field:
                metadata["Metadata"]["visibleLines"] = field["visibleLines"]

            resp = requests.post(tooling_url, headers=headers, json=metadata)
            if resp.status_code == 201:
                logger.info(f"[CREATED FIELD] {field['name']}")
            else:
                logger.error(f"[ERROR] {field['name']} → {resp.text}")

        set_redis_json(cache_key, True, expire=86400)
        return True
    except Exception as e:
        logger.warning(f"[Salesforce] Error ensuring custom fields: {e}")
        return False

# ----------------------------
# UPSERT LEAD
# ----------------------------
def upsert_lead_by_phone(phone: str, data: Dict[str, Any], client_id, client_secret, domain=None):

    def _perform(token):
        if not token:
            logger.warning("[Salesforce] No valid token available. Skipping lead upsert.")
            return None
        headers = get_headers(token)
        instance_url = token["instance_url"]

        allowed_fields = get_lead_fields(instance_url, headers)
        clean_data = filter_valid_fields(data, allowed_fields)

        # QUERY
        query = f"SELECT Id FROM Lead WHERE Phone = '{phone}' LIMIT 1"
        url = f"{instance_url}/services/data/{SF_VERSION}/query"

        resp = requests.get(url, headers=headers, params={"q": query})
        resp.raise_for_status()

        records = resp.json().get("records", [])

        # UPDATE
        if records:
            lead_id = records[0]["Id"]
            update_url = f"{instance_url}/services/data/{SF_VERSION}/sobjects/Lead/{lead_id}"

            resp_update = requests.patch(update_url, headers=headers, json=clean_data)

            if resp_update.status_code != 204:
                logger.error(resp_update.text)
                raise Exception(resp_update.text)

            return {"status": "updated", "id": lead_id}

        # CREATE
        create_data = {
            "LastName": clean_data.get("LastName", "Unknown"),
            "Company": clean_data.get("Company", "N/A"),
            "Phone": phone,
            **clean_data
        }

        create_url = f"{instance_url}/services/data/{SF_VERSION}/sobjects/Lead"
        resp_create = requests.post(create_url, headers=headers, json=create_data)

        if resp_create.status_code != 201:
            logger.error(f"CREATE ERROR: {resp_create.text}")
            raise Exception(resp_create.text)

        return {"status": "created", "id": resp_create.json()["id"]}

    try:
        token = get_salesforce_token(client_id, client_secret, domain)
        if not token:
            logger.warning("[Salesforce] No token obtained. Skipping lead upsert.")
            return None
        return _perform(token)
    except requests.exceptions.HTTPError as e:
        if e.response and e.response.status_code == 401:
            logger.warning("Token expired → refreshing")
            token = get_salesforce_token(client_id, client_secret, domain, force_refresh=True)
            if token:
                return _perform(token)
        logger.warning(f"[Salesforce] Upsert HTTP exception ({e}). Bypassing.")
        return None
    except Exception as e:
        logger.warning(f"[Salesforce] Upsert exception ({e}). Bypassing.")
        return None

def check_salesforce_connection(salesforce_client_id, salesforce_client_secret, salesforce_domain: Optional[str] = None, **kwargs):
    """
    Verifies connection to Salesforce and returns Identity info.
    """
    try:
        # Force refresh to ensure valid connection check
        token_data = get_salesforce_token(salesforce_client_id, salesforce_client_secret, salesforce_domain, force_refresh=True)
        
        access_token = token_data["access_token"]
        id_url = token_data.get("id")
        headers = {"Authorization": f"Bearer {access_token}"}
        
        resp = requests.get(id_url, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Connection check failed: {e}")
        raise

def bulk_sync_crm(leads: list, salesforce_client_id, salesforce_client_secret, salesforce_domain: Optional[str] = None, **kwargs):
    """
    Bulk syncs a list of leads.
    """
    results = []
    errors = []
    
    for lead in leads:
        phone = lead.get("Phone")
        if not phone:
            errors.append({"error": "Missing Phone", "data": lead})
            continue

        try:
            res = upsert_lead_by_phone(phone, lead, salesforce_client_id, salesforce_client_secret, salesforce_domain)
            results.append({"phone": phone, "result": res})
        except Exception as e:
            errors.append({"phone": phone, "error": str(e)})

    return {
        "synced": len(results),
        "failed": len(errors),
        "details": results,
        "errors": errors
    }

# ----------------------------
# FETCH LEADS
# ----------------------------
def fetch_leads(client_id, client_secret, domain=None, limit=50):
    token = get_salesforce_token(client_id, client_secret, domain)
    headers = get_headers(token)
    instance_url = token["instance_url"]

    query = f"""
    SELECT Id, Name, Phone, Company 
    FROM Lead 
    WHERE Phone != null AND IsConverted = false 
    LIMIT {limit}
    """

    url = f"{instance_url}/services/data/{SF_VERSION}/query"
    resp = requests.get(url, headers=headers, params={"q": query})
    resp.raise_for_status()

    return [
        {"name": r["Name"], "phone": r["Phone"]}
        for r in resp.json().get("records", [])
    ]