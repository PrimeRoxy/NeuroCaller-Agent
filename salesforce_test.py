import requests
import logging
from dotenv import load_dotenv
import os

load_dotenv()

def get_salesforce_token(client_id, client_secret, domain):
    import requests

    url = f"{domain}/services/oauth2/token"

    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }

    response = requests.post(url, data=payload)

    if response.status_code != 200:
        print("Token Error:", response.text)
        response.raise_for_status()

    return response.json()

def create_leads(instance_url, access_token, leads):
    import requests

    url = f"{instance_url}/services/data/v59.0/composite/sobjects"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    records = []
    for lead in leads:
        records.append({
            "attributes": {"type": "Lead"},
            **lead
        })

    payload = {
        "allOrNone": False,
        "records": records
    }

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code not in [200, 201]:
        print("Lead Creation Error:", response.text)
        response.raise_for_status()

    return response.json()

leads_data = []

phones = [
    "9113797240",
    "+919113797240",
    "9876543210",
    "+919876543210",
    "9123456789"
]

for i, phone in enumerate(phones):
    lead = {
        "Company": "N/A",
        "LastName": f"Lead_{i+1}",
        "Status": "Open",
        "Phone": phone,

        "Agent_Number__c": "AGENT_001",
        "Call_Attempts__c": 1,
        "Duration_Seconds__c": 120,
        "Next_Best_Action__c": "Follow up",
        "Follow_Up_required__c": "Yes",
        "Outcome__c": "Interested",
        "Outcome_Reason__c": "Customer asked for callback"
    }
    leads_data.append(lead)

# Metadata for required custom fields on Lead object
REQUIRED_LEAD_FIELDS = [
    {"name": "Agent_Number__c", "label": "Agent Number", "type": "Text", "length": 255},
    {"name": "Call_Attempts__c", "label": "Call Attempts", "type": "Number", "precision": 18, "scale": 0},
    {"name": "Duration_Seconds__c", "label": "Duration (Seconds)", "type": "Number", "precision": 18, "scale": 0},
    {"name": "Next_Best_Action__c", "label": "Next Best Action", "type": "Text", "length": 255},
    {"name": "Follow_Up_required__c", "label": "Follow Up Required", "type": "Text", "length": 10},
    {"name": "Outcome__c", "label": "Call Outcome", "type": "Text", "length": 255},
    {"name": "Outcome_Reason__c", "label": "Outcome Reason", "type": "LongTextArea", "length": 32000, "visibleLines": 3},
]

def ensure_custom_fields_exist(client_id, client_secret, domain, object_name="Lead"):
    logging.info(f"Ensuring custom fields exist for {object_name}...")

    try:
        token_data = get_salesforce_token(client_id, client_secret, domain)
        access_token = token_data["access_token"]
        instance_url = token_data["instance_url"]

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        # 1. Describe object
        describe_url = f"{instance_url}/services/data/v59.0/sobjects/{object_name}/describe"
        resp = requests.get(describe_url, headers=headers)
        resp.raise_for_status()

        existing_fields = {f["name"].lower() for f in resp.json()["fields"]}

        # 2. Missing fields
        missing_fields = [f for f in REQUIRED_LEAD_FIELDS if f["name"].lower() not in existing_fields]

        if not missing_fields:
            logging.info("All fields already exist.")
            return True

        # 3. Create fields
        tooling_url = f"{instance_url}/services/data/v59.0/tooling/sobjects/CustomField"

        for field in missing_fields:
            logging.info(f"Creating field {field['name']}")

            metadata = {
                "label": field["label"],
                "type": field["type"]
            }

            if field["type"] == "Text":
                metadata["length"] = field["length"]
            elif field["type"] == "Number":
                metadata["precision"] = field["precision"]
                metadata["scale"] = field["scale"]
            elif field["type"] == "LongTextArea":
                metadata["length"] = field["length"]
                metadata["visibleLines"] = field["visibleLines"]

            payload = {
                "FullName": f"{object_name}.{field['name']}",
                "Metadata": metadata
            }

            r = requests.post(tooling_url, headers=headers, json=payload)

            if r.status_code == 201:
                logging.info(f"Created {field['name']}")
            else:
                logging.error(r.text)

        return True

    except Exception as e:
        logging.error(f"Error: {e}")
        return False

CLIENT_ID = os.getenv("SALESFORCE_CLIENT_ID")
CLIENT_SECRET = os.getenv("SALESFORCE_CLIENT_SECRET")

DOMAIN = "https://orgfarm-af1896b67b-dev-ed.develop.my.salesforce.com"

# Step 1: Get token
token_data = get_salesforce_token(CLIENT_ID, CLIENT_SECRET, DOMAIN)

access_token = token_data["access_token"]
instance_url = token_data["instance_url"]

print("Connected to:", instance_url)

# Step 2: Ensure fields
ensure_custom_fields_exist(CLIENT_ID, CLIENT_SECRET, DOMAIN)

# Step 3: Create leads
result = create_leads(instance_url, access_token, leads_data)

print("Lead Creation Result:")
print(result)