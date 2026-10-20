from fastapi import APIRouter, HTTPException, Depends, Body, Query
from services.config_service import ConfigService
from services.salesforce_service import check_salesforce_connection, bulk_sync_crm
from services.call_service_voicelink import calls_collection
import logging
from typing import List, Dict, Any
from datetime import datetime
from bson import ObjectId

router = APIRouter(tags=["Salesforce"])
logger = logging.getLogger(__name__)

def get_salesforce_creds(organisation_id: str, user_id: str = None):
    # Retrieve config
    try:
        config = ConfigService.get_config(organisation_id=organisation_id, user_id=user_id)
    except Exception:
        # If specific user config fails, try without user_id if user_id was provided
        if user_id:
            try:
                config = ConfigService.get_config(organisation_id=organisation_id, user_id=None)
            except Exception:
                config = None
        else:
             config = None
             
    if not config:
        # If still not found, try finding ANY config for this org (fallback)
        raise HTTPException(status_code=404, detail=f"Organisation config not found for {organisation_id}")

    # Extract Credentials
    # Prefer 'salesforce_creds' dict (new schema) but fallback to top-level keys
    creds_dict = config.get("salesforce_creds", {})
    
    client_id = creds_dict.get("client_id") or config.get("salesforce_client_id")
    client_secret = creds_dict.get("client_secret") or config.get("salesforce_client_secret")
    domain = creds_dict.get("domain") or config.get("salesforce_domain")
    
    username = creds_dict.get("username") or config.get("salesforce_username")
    password = creds_dict.get("password") or config.get("salesforce_password")
    security_token = creds_dict.get("security_token") or config.get("salesforce_security_token")

    creds = {
        "salesforce_client_id": client_id,
        "salesforce_client_secret": client_secret,
        "salesforce_domain": domain,
        "salesforce_username": username,
        "salesforce_password": password,
        "salesforce_security_token": security_token
    }

    # Don't strictly require domain if we have a default in the service, 
    # but client_id and client_secret are usually required.
    if not all([client_id, client_secret]):
         raise HTTPException(status_code=400, detail="Salesforce credentials (client_id/client_secret) incomplete in configuration")

    return creds

@router.get("/api/salesforce/check")
async def check_connection(
    organisation_id: str = Query(..., description="Organisation ID"),
    user_id: str = Query(None, description="User ID for config retrieval (optional)") 
):
    """
    Check Salesforce connection and retrieve identity/user data.
    """
    creds = get_salesforce_creds(organisation_id, user_id)
    try:
        data = check_salesforce_connection(**creds)
        return {"status": "success", "data": data}
    except Exception as e:
        logger.error(f"Salesforce check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/salesforce/sync-status/{organisation_id}")
async def get_sync_status(
    organisation_id: str,
    user_id: str = Query(None, description="User ID (optional)")
):
    """
    Get the count of calls by CRM sync status for an organisation.
    """
    try:
        match_stage = {"organisation_id": organisation_id}
        if user_id:
            match_stage["user_id"] = user_id
            
        pipeline = [
            {"$match": match_stage},
            {"$group": {"_id": "$crm_sync", "count": {"$sum": 1}}}
        ]
        results = list(calls_collection.aggregate(pipeline))
        
        # Format result
        stats = {
            "total": 0,
            "pending": 0,
            "synced": 0,
            "failed": 0,
            "not_synced": 0 
        }
        
        for r in results:
            status = r["_id"]
            count = r["count"]
            stats["total"] += count
            
            if status == "pending":
                stats["pending"] = count
            elif status == "synced":
                stats["synced"] = count
            elif status == "failed":
                stats["failed"] = count
            else:
                stats["not_synced"] += count 
                
        return {"organisation_id": organisation_id, "stats": stats}
    except Exception as e:
        logger.error(f"Error fetching sync status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/salesforce/sync-pending/{organisation_id}")
async def sync_pending_calls(
    organisation_id: str,
    user_id: str = Query(None, description="User ID (optional)"),
    limit: int = Query(50, description="Max records to sync in one go")
):
    """
    Manually trigger sync for pending or failed calls for an organisation.
    """
    creds = get_salesforce_creds(organisation_id, user_id)
    
    # 1. Fetch pending records
    query = {
        "organisation_id": organisation_id,
        "crm_sync": {"$in": ["pending", "failed"]}
    }
    
    if user_id:
        query["user_id"] = user_id
    
    # Fetch records
    cursor = calls_collection.find(query).limit(limit)
    calls = list(cursor)
    
    if not calls:
        return {"message": "No pending records found to sync.", "synced_count": 0}
        
    # 2. Map to Salesforce Lead Data
    leads_to_sync = []
    
    # Status Mapping (Simple version)
    status_map = {
        "Qualified": "Qualified",
        "Follow-up required": "Working - Contacted",
        "Not interested": "Closed - Not Converted",
        "Unreachable": "Open - Not Contacted"
    }
    
    for call in calls:
        phone = call.get("to_number")
        if not phone:
            continue
            
        outcome = call.get("outcome", "Unreachable")
        sf_status = status_map.get(outcome, "Open - Not Contacted")
        
        lead_data = {
            "LastName": call.get("lead_name") or "Unknown Lead",
            "Company": "N/A",
            "Status": sf_status,
            "Phone": phone,
            "Outcome__c": outcome,
            "Outcome_Reason__c": call.get("reason", ""),
            "Duration_Seconds__c": call.get("duration", 0),
            "Agent_Number__c": call.get("from_number", "")
        }
        
        email = call.get("extracted_email")
        if email:
            lead_data["Email"] = email
            
        # Store internal call_uuid for updating later
        lead_data["_call_uuid"] = call.get("call_uuid")
        
        leads_to_sync.append(lead_data)
        
    if not leads_to_sync:
        return {"message": "No valid leads found in pending records.", "synced_count": 0}

    # 3. Perform Bulk Sync
    try:
        sync_result = bulk_sync_crm(leads_to_sync, **creds)
    except Exception as e:
        logger.error(f"Bulk sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
    # 4. Update Mongo based on results
    updated_count = 0
    failed_count = 0
    
    # Helper to find which call doc corresponds to the synced item
    # Since bulk_sync_crm returns details with 'phone', we match by phone.
    # Note: If multiple calls share one phone, this might update all of them if we are not careful.
    # But for now, we iterate through details and update corresponding calls.
    
    # Create a map of phone -> list of call_uuids from our batch
    phone_uuid_map = {}
    for l in leads_to_sync:
        p = l["Phone"]
        if p not in phone_uuid_map:
            phone_uuid_map[p] = []
        phone_uuid_map[p].append(l["_call_uuid"])
        
    # Process Successes
    for item in sync_result.get("details", []):
        phone = item["phone"]
        uuids = phone_uuid_map.get(phone, [])
        for uuid in uuids:
            calls_collection.update_one(
                {"call_uuid": uuid},
                {"$set": {"crm_sync": "synced", "crm_sync_date": datetime.utcnow()}}
            )
            updated_count += 1
                
    # Process Failures
    for item in sync_result.get("errors", []):
        phone = item["phone"] # This might be missing if error was generic, but bulk_sync_crm returns it
        error_msg = item.get("error", "Unknown Error")
        uuids = phone_uuid_map.get(phone, [])
        for uuid in uuids:
            calls_collection.update_one(
                {"call_uuid": uuid},
                {"$set": {"crm_sync": "failed", "crm_sync_error": error_msg}}
            )
            failed_count += 1

    return {
        "status": "completed",
        "processed": len(leads_to_sync),
        "synced": updated_count,
        "failed": failed_count,
        "details": sync_result
    }

@router.post("/api/salesforce/sync")
async def bulk_sync(
    payload: Dict[str, Any] = Body(...)
):
    """
    Bulk sync leads to Salesforce via explicit payload.
    Payload: { "organisation_id": "...", "user_id": "...", "leads": [ { "Phone": "...", ... } ] }
    """
    org_id = payload.get("organisation_id")
    user_id = payload.get("user_id") # Optional
    leads = payload.get("leads", [])
    
    if not org_id:
        raise HTTPException(status_code=400, detail="organisation_id is required")
        
    creds = get_salesforce_creds(org_id, user_id)
    
    try:
        result = bulk_sync_crm(leads, **creds)
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error(f"Salesforce bulk sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
