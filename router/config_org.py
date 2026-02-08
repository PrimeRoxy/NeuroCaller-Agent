import logging
import os
import uuid
from starlette.concurrency import run_in_threadpool
from urllib.parse import urlparse
from bson import ObjectId
from fastapi import File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime
from fastapi import APIRouter
import boto3
from dotenv import load_dotenv
from services.org_service import estimate_text_chars, extract_text_from_file
from services.prompt_builder import build_universal_sales_system_message
from services.plivo_number import get_available_countries, get_rented_numbers
load_dotenv()
from call.plivo import orgcalls_collection,calls_collection
router = APIRouter()

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("S3_REGION")
S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET")

s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)


class FileResource(BaseModel):
    file: str
    uploaded_at: datetime

class AllyroidConfig(BaseModel):
    organisation_id: str
    organisation_name: str
    welcome_message: str
    country_code: str
    phone_number: str
    uploaded_resources: List[FileResource] = []

MAX_TOTAL_CHARS = 40000 # Limit to 40k characters (~10k tokens)
@router.post("/api/allyroid/config")
async def upsert_config(
    user_id: str = Form(...),
    user_name: str = Form(None),
    organisation_id: str = Form(...),
    organisation_name: str = Form(None),
    welcome_message: str = Form(None),
    country_code: str = Form(None),
    phone_number: str = Form(None),
    file: UploadFile = File(None),
):
    """
    Create or update configuration for an organisation and user.
    - If config exists: Updates it (optionally with new file)
    - If config doesn't exist: Creates new config
    
    Extract text from uploaded file, build system prompt,
    upload file to S3, and save/update all config data in MongoDB.
    """

    # Check for existing configuration
    existing = orgcalls_collection.find_one({
        "organisation_id": organisation_id,
        "user_id": user_id
    })
    
    is_update = existing is not None
    uploaded_resources = existing.get("uploaded_resources", []) if is_update else []

    # ---- Process the uploaded file (if provided) ----
    if file:
        content_bytes = await file.read()
        estimated_len = estimate_text_chars(content_bytes, file.filename)
        if estimated_len > MAX_TOTAL_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"File text exceeds {MAX_TOTAL_CHARS} characters (estimated)."
            )

        extracted_text = extract_text_from_file(content_bytes, file.filename)

        # Enforce character limit
        if len(extracted_text) > MAX_TOTAL_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"File text exceeds {MAX_TOTAL_CHARS} characters."
            )

        # ✅ Convert extracted text into a system prompt
        try:
            system_prompt = build_universal_sales_system_message(extracted_text, welcome_message=welcome_message)
        except Exception as e:
            logging.error(f"Error creating system message: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error generating system message: {e}"
            )

        # Reset stream before upload
        await file.seek(0)

        # Upload to S3
        file_ext = os.path.splitext(file.filename)[1]
        s3_key = f"allyroid_uploads/{organisation_id}/{uuid.uuid4()}{file_ext}"

        try:
            s3_client.upload_fileobj(
                Fileobj=file.file,
                Bucket=S3_BUCKET_NAME,
                Key=s3_key,
                ExtraArgs={"ContentType": file.content_type}
            )
        except Exception as e:
            logging.error(f"S3 upload failed: {e}")
            raise HTTPException(status_code=500, detail=f"File upload failed: {e}")

        file_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"

        # Add new resource to the list
        new_resource = {
            "file_name": file.filename,
            "file_url": file_url,
            "file_data": system_prompt,  # ✅ Save system prompt here
            "uploaded_at": datetime.utcnow()
        }
        
        # For updates: append new file, for creates: start fresh list
        uploaded_resources.append(new_resource)

    # ---- Prepare data for MongoDB ----
    data = {
        "user_id": user_id,
        "user_name": user_name,
        "organisation_id": organisation_id,
        "organisation_name": organisation_name,
        "welcome_message": welcome_message,
        "country_code": country_code,
        "phone_number": phone_number,
        "uploaded_resources": uploaded_resources,
        "updated_at": datetime.utcnow()
    }

    # ---- Create or Update ----
    if is_update:
        # Update existing configuration
        result = orgcalls_collection.update_one(
            {"organisation_id": organisation_id, "user_id": user_id},
            {"$set": data}
        )
        
        if result.modified_count > 0 or result.matched_count > 0:
            return {
                "message": "Configuration updated successfully",
                "id": str(existing["_id"]),
                "action": "updated",
                "file_uploaded": file is not None
            }
        else:
            raise HTTPException(status_code=500, detail="Update failed")
    else:
        # Create new configuration
        data["created_at"] = datetime.utcnow()
        result = orgcalls_collection.insert_one(data)
        
        return {
            "message": "Configuration created successfully",
            "id": str(result.inserted_id),
            "action": "created",
            "file_uploaded": file is not None,
            "file prompt": system_prompt if file else None
        }

@router.get("/api/allyroid/config/{user_id}/{organisation_id}")
async def get_config(user_id: str,organisation_id: str):
    """
    Fetch Allyroid configuration for a given organisation_id.
    """
    results = orgcalls_collection.find(
        {"user_id": user_id, "organisation_id": organisation_id},  # filter
        {"_id": 0}  # projection to exclude _id
    )
    cleaned_results = list(results)

    if not results:
        raise HTTPException(status_code=404, detail="Configuration not found for this organisation.")
    
    return {
        "message": "Configuration fetched successfully",
        "data": cleaned_results
    }
    

@router.delete("/api/allyroid/config/{organisation_id}")
async def delete_config(user_id: str, organisation_id: str):
    """
    Delete configuration for a given organisation_id.
    (Optionally, delete related S3 files if needed)
    """
    config = orgcalls_collection.find_one({"user_id": user_id},{"organisation_id": organisation_id})
    if not config:
        raise HTTPException(status_code=404, detail="Configuration not found for this organisation.")

    # --- (Optional) Delete S3 files ---
    for res in config.get("uploaded_resources", []):
        try:
            s3_key = "/".join(res["file_url"].split(".com/")[1:])
            s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        except Exception:
            pass  # ignore file deletion errors for safety

    orgcalls_collection.delete_one({"organisation_id": organisation_id})
    return {"message": f"Configuration for organisation_id '{organisation_id}' deleted successfully."}


def convert_objectid(data):
    if isinstance(data, list):
        return [convert_objectid(item) for item in data]
    elif isinstance(data, dict):
        return {key: convert_objectid(value) for key, value in data.items()}
    elif isinstance(data, ObjectId):
        return str(data)
    else:
        return data

def _s3_key_from_file_url(file_url: str) -> str:
    """
    Parses S3 key from a URL like:
    https://{bucket}.s3.{region}.amazonaws.com/{key}
    """
    parsed = urlparse(file_url)
    # path starts with "/{key}"
    key = parsed.path.lstrip("/")
    if not key:
        raise ValueError(f"Could not parse S3 key from file_url: {file_url}")
    return key

async def _download_s3_bytes(bucket: str, key: str) -> bytes:
    """
    Download object bytes from S3 without blocking the event loop too long.
    """
    def _get():
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()

    return await run_in_threadpool(_get)

@router.post("/api/allyroid/config/{organisation_id}")
async def update_config(
    organisation_id: str,
    user_id: str,
    user_name: Optional[str] = Form(None),
    welcome_message: Optional[str] = Form(None),
    country_code: Optional[str] = Form(None),
    phone_number: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    existing = orgcalls_collection.find_one({
        "organisation_id": organisation_id,
        "user_id": user_id
    })
    if not existing:
        raise HTTPException(status_code=404, detail="Configuration not found for this organisation.")

    update_fields = {}

    if user_name is not None:
        update_fields["user_name"] = user_name
    if welcome_message is not None:
        update_fields["welcome_message"] = welcome_message
    if country_code is not None:
        update_fields["country_code"] = country_code
    if phone_number is not None:
        update_fields["phone_number"] = phone_number

    uploaded_resources = existing.get("uploaded_resources", [])

    # Detect welcome message change (only if caller sent welcome_message)
    welcome_changed = (
        welcome_message is not None
        and welcome_message != existing.get("welcome_message")
    )

    # CASE 1: New file uploaded (your existing behavior)
    if file:
        content_bytes = await file.read()
        if not content_bytes:
            raise HTTPException(status_code=400, detail=f"Empty file uploaded: {file.filename}")

        extracted_text = extract_text_from_file(content_bytes, file.filename)
        extracted_chars = len(extracted_text)

        if extracted_chars > MAX_TOTAL_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"{file.filename}: exceeds {MAX_TOTAL_CHARS} characters ({extracted_chars} extracted)."
            )

        try:
            system_prompt = build_universal_sales_system_message(
                extracted_text,
                welcome_message=welcome_message
            )
        except Exception as e:
            logging.error(f"Error creating system message for {file.filename}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error generating system message for {file.filename}: {e}"
            )

        await file.seek(0)

        file_ext = os.path.splitext(file.filename)[1]
        s3_key = f"allyroid_uploads/{organisation_id}/{uuid.uuid4()}{file_ext}"

        try:
            s3_client.upload_fileobj(
                Fileobj=file.file,
                Bucket=S3_BUCKET_NAME,
                Key=s3_key,
                ExtraArgs={"ContentType": file.content_type}
            )
            file_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"

            uploaded_resources.append({
                "file_name": file.filename,
                "file_url": file_url,
                "file_data": system_prompt,
                "uploaded_at": datetime.utcnow(),
                "extracted_chars": extracted_chars,
                # Optional (recommended): store key explicitly for future use
                "s3_key": s3_key,
            })
        except Exception as e:
            logging.error(f"S3 upload failed for {file.filename}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload {file.filename}: {str(e)}"
            )

        update_fields["uploaded_resources"] = uploaded_resources

    # CASE 2: No new file, but welcome message changed
    elif welcome_changed:
        if not uploaded_resources:
            raise HTTPException(
                status_code=400,
                detail="Welcome message changed but no previously uploaded file exists to regenerate prompts."
            )

        # Use the most recent uploaded file (you can switch to regenerating all if desired)
        last_resource = uploaded_resources[-1]

        file_url = last_resource.get("file_url")
        file_name = last_resource.get("file_name")

        if not file_url or not file_name:
            raise HTTPException(
                status_code=500,
                detail="Stored uploaded resource missing file_url/file_name; cannot fetch from S3."
            )

        try:
            # Prefer stored s3_key if present
            s3_key = last_resource.get("s3_key") or _s3_key_from_file_url(file_url)

            content_bytes = await _download_s3_bytes(S3_BUCKET_NAME, s3_key)
            if not content_bytes:
                raise HTTPException(
                    status_code=500,
                    detail=f"Downloaded empty object from S3 for key={s3_key}"
                )

            extracted_text = extract_text_from_file(content_bytes, file_name)
            extracted_chars = len(extracted_text)

            if extracted_chars > MAX_TOTAL_CHARS:
                raise HTTPException(
                    status_code=400,
                    detail=f"{file_name}: exceeds {MAX_TOTAL_CHARS} characters ({extracted_chars} extracted)."
                )

            system_prompt = build_universal_sales_system_message(
                extracted_text,
                welcome_message=welcome_message
            )

            # Update the same “task output” as before (file_data etc.)
            last_resource["file_data"] = system_prompt
            last_resource["extracted_chars"] = extracted_chars
            last_resource["regenerated_at"] = datetime.utcnow()

            uploaded_resources[-1] = last_resource
            update_fields["uploaded_resources"] = uploaded_resources

        except HTTPException:
            raise
        except Exception as e:
            logging.error(f"Failed to regenerate system prompt from S3 for {file_name}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to regenerate system prompt from stored file: {str(e)}"
            )

    # Always bump updated_at
    update_fields["updated_at"] = datetime.utcnow()

    orgcalls_collection.update_one(
        {"organisation_id": organisation_id, "user_id": user_id},
        {"$set": update_fields}
    )

    updated_doc = orgcalls_collection.find_one({
        "organisation_id": organisation_id,
        "user_id": user_id
    })
    updated_doc = convert_objectid(updated_doc)

    return {"message": "Configuration updated successfully", "data": updated_doc}
@router.get("/countries")
def list_countries():
    """
    Endpoint to list countries where Plivo offers phone numbers.
    """
    countries = get_available_countries()
    return {"available_countries": countries}

@router.get("/rented-numbers")
def list_rented_numbers(country_code: Optional[str] = Query(None, description="ISO country code to filter numbers")):
    """
    Endpoint to list rented phone numbers, optionally filtered by country code.
    """
    try:
        numbers = get_rented_numbers(country_code)
        return {"rented_numbers": numbers}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/calls/by-user-org")
def get_calls_by_user_org(
    organisation_id: str = Query(..., description="Organisation ID"),
    user_id: Optional[str] = Query(None, description="User ID (optional)"),
):
    """
    Fetch call records from MongoDB for a specific organisation_id.
    If `user_id` is provided, filter by both organisation_id and user_id.
    """
    try:
        # Build the MongoDB filter (only include user_id when provided)
        filt = {"organisation_id": organisation_id}
        if user_id:
            filt["user_id"] = user_id

        # Query MongoDB (exclude _id for cleaner response)
        records = list(calls_collection.find(filt, {"_id": 0}))
        count = len(records)

        if count == 0:
            return {
                "status": "success",
                "message": "No call records found."
                           + (" for this user and organisation." if user_id else " for this organisation."),
                "organisation_id": organisation_id,
                "user_id": user_id,
                "count": 0,
                "calls": []
            }

        return {
            "status": "success",
            "organisation_id": organisation_id,
            "user_id": user_id,
            "count": count,
            "calls": records
        }

    except Exception as e:
        logging.error(f"Error fetching call data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching call data: {str(e)}")

@router.delete("/api/delete/file")
async def delete_file_from_config(
    organisation_id: str = Query(...),
    user_id: str = Query(...),
):
    """
    Remove the uploaded file reference from organisation configuration in MongoDB.
    Does NOT delete the file from S3.
    Since only one file is stored per configuration, this simply clears uploaded_resources.
    """

    logging.info(f"[DELETE FILE - DB ONLY] org={organisation_id}, user={user_id}")

    # Find configuration for this org and user
    existing = orgcalls_collection.find_one({
        "organisation_id": organisation_id,
        "user_id": user_id
    })

    if not existing:
        logging.error(f"Config not found for org: {organisation_id}, user: {user_id}")
        raise HTTPException(status_code=404, detail="Configuration not found.")

    uploaded_resources = existing.get("uploaded_resources", [])
    if not uploaded_resources:
        logging.warning(f"No file found for org: {organisation_id}, user: {user_id}")
        raise HTTPException(status_code=404, detail="No file found in configuration.")

    # There’s only one file to remove
    file_to_delete = uploaded_resources[0]
    file_name = file_to_delete.get("file_name")

    # Clear uploaded_resources in MongoDB
    result = orgcalls_collection.update_one(
        {"organisation_id": organisation_id, "user_id": user_id},
        {
            "$set": {
                "uploaded_resources": [],
                "updated_at": datetime.utcnow()
            }
        }
    )

    if result.modified_count == 0:
        logging.error(f"MongoDB update failed for org: {organisation_id}, user: {user_id}")
        raise HTTPException(status_code=500, detail="Failed to update configuration.")

    logging.info(f"[DELETE FILE - DB ONLY] Removed file '{file_name}' for org={organisation_id}")

    return {
        "message": "File reference removed from configuration (DB only).",
        "deleted_file": file_name,
        "organisation_id": organisation_id,
        "user_id": user_id
    }

@router.post("/api/allyroid/validate-file")
async def validate_uploaded_file(file: UploadFile = File(...)) -> Dict:
    """
    Validate uploaded file:
    - Estimates text length (cheap)
    - Checks character limit
    - Returns validation summary only (no DB or S3)
    """

    # Step 1: Read file bytes
    content_bytes = await file.read()
    filename = file.filename
    file_ext = os.path.splitext(filename)[1].lower()

    if not content_bytes:
        raise HTTPException(status_code=400, detail="Empty file uploaded.")

    # Step 2: Estimate chars (returns int)
    try:
        estimated_chars = estimate_text_chars(content_bytes, filename)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to process file: {str(e)}")

    # Step 3: Enforce character limit (NO len() here)
    if estimated_chars > MAX_TOTAL_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"File text exceeds {MAX_TOTAL_CHARS} characters ({estimated_chars} found)."
        )

    # Step 4: Return validation summary
    return {
        "filename": filename,
        "file_size_bytes": len(content_bytes),
        "extracted_chars": estimated_chars,  # already an int
        "status": "valid",
        "message": "File validated successfully",
        "file_type": file_ext,
    }
