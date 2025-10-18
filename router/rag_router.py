from fastapi import APIRouter, Body, UploadFile, File, HTTPException, Form, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from core.config import set_redis_json, get_redis_json
import json
import uuid
import logging
import time

from rag.loader import extract_text
from rag.qdrant import (
    store_documents, 
    reset_index, 
    get_collection_info, 
    store_document_bloom_parallel
)
from qdrant_client import QdrantClient
from core.config import get_settings

settings = get_settings()

QDRANT_HOST = settings.QDRANT_HOST
QDRANT_PORT = settings.QDRANT_PORT
COLLECTION_NAME = settings.QDRANT_COLLECTION_NAME
VECTOR_SIZE = settings.QDRANT_VECTOR_SIZE

logging.basicConfig(level=logging.INFO)

router = APIRouter()

def _get_client():
    """Get Qdrant client instance"""
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, check_compatibility=False)

class CollectionInfoResponse(BaseModel):
    collection_name: str
    exists: bool
    points_count: Optional[int] = None
    indexed_vectors_count: Optional[int] = None
    status: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    segments_count: Optional[int] = None

class KnowledgeInjectionRequest(BaseModel):
    content: dict
    source: str

class CollectionStatsResponse(BaseModel):
    collection_name: str
    exists: bool
    total_points: Optional[int] = None
    indexed_vectors: Optional[int] = None
    status: Optional[str] = None
    vector_size: Optional[int] = None
    distance_metric: Optional[str] = None
    message: Optional[str] = None

class CollectionHealthResponse(BaseModel):
    collection_name: str
    healthy: bool
    status: str
    points_count: int
    indexed_vectors: Optional[int] = None

class ChunkStoreRequest(BaseModel):
    chunks: List[str]
    metadata: List[Dict[str, Any]]
    source: str

class KnowledgeInjectionBloomRequest(BaseModel):
    text: str = Field(..., example="Document content here")
    source: str = Field(..., example="manual_seed")
    document_name: str = Field(..., example="My Document")
    document_id: str = Field(..., example="unique-doc-id-123")

@router.post("/knowledge_injection")
async def upload(
    file: UploadFile = File(...),
    document_name: str = Form(None),
    document_id: str = Form(None)
):
    try:
        text = extract_text(file.file, file.filename)
        logging.info(f"Extracted text from {file.filename} with chunks {text} of size {len(text)}")
        store_documents(
            text,
            source=file.filename,
            document_name=document_name or file.filename,
            document_id=document_id
        )
        return {"status": "injected", "file": file.filename, "document_name": document_name or file.filename}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class LoadDocsRequest(BaseModel):
    text: str = Field(..., example="AI stands for Artificial Intelligence...")
    source: str = Field(..., example="manual_seed")



@router.post("/reset-index")
async def reset():
    if reset_index():
        return {"status": "reset"}
    else:
        raise HTTPException(status_code=400, detail="Collection did not exist")

@router.get("/collection/info", response_model=CollectionInfoResponse)
async def get_collection_info_endpoint():
    try:
        info = get_collection_info()
        return CollectionInfoResponse(**info)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve collection information: {str(e)}")

@router.get("/collection/stats")
async def get_collection_stats():
    try:
        client = _get_client()
        if not client.collection_exists(COLLECTION_NAME):
            return {
                "collection_name": COLLECTION_NAME,
                "exists": False,
                "message": "Collection does not exist"
            }
        info = client.get_collection(COLLECTION_NAME)
        return {
            "collection_name": COLLECTION_NAME,
            "exists": True,
            "total_points": info.points_count,
            "indexed_vectors": getattr(info, 'indexed_vectors_count', 0),
            "status": getattr(info, 'status', 'unknown'),
            "vector_size": info.config.params.vectors.size,
            "distance_metric": info.config.params.vectors.distance.value
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get collection stats: {str(e)}")

# @router.delete("/collection/remove-duplicates")
# async def remove_duplicate_points():
#     try:
#         result = remove_duplicates(client=_get_client(), collection_name=COLLECTION_NAME)
#         return {"message": "Duplicates removed", "details": result}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))


def process_bloom_upload(text, source, document_name, document_id):
    store_document_bloom_parallel(
        text=text,
        source=source,
        document_name=document_name,
        document_id=document_id
    )

@router.post("/knowledge_injection_bloom")
async def knowledge_injection_bloom(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_name: str = Form(None),
    document_id: str = Form(None)
):
    try:
        start_time = time.time()
        if not document_id:
            document_id = str(uuid.uuid4())

        text = extract_text(file.file, file.filename)
        logging.info(f"Extracted text from {file.filename} with chunks {text} of size {len(text)}")
        background_tasks.add_task(
            process_bloom_upload,
            text=text,
            source=file.filename,
            document_name=document_name or file.filename,
            document_id=document_id
        )
        
        total_time = time.time() - start_time
        
        payload = {
            "status": "processing",
            "document_name": document_name,
            "document_id": document_id,
            "message": "Document uploaded successfully. Processing started in background.",
            "total_time": total_time
        }
        redis_key = f"document_{document_id}"
        set_redis_json(redis_key, payload)
        return payload
        
    except Exception as e:
        logging.error(f"Error in knowledge_injection_bloom: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get(
    "/document_status/{document_id}",
    summary="Get document processing status",
    description="Get the current processing status of a document by its ID from Redis cache."
)
async def get_document_status(document_id: str):
    """
    Get document processing status from Redis
    
    Args:
        document_id: The unique identifier of the document
        
    Returns:
        Document processing status and metadata
    """
    try:
        redis_key = f"document_{document_id}"
        document_data = get_redis_json(redis_key)
        
        if not document_data:
            raise HTTPException(
                status_code=404,
                detail=f"Document with ID '{document_id}' not found"
            )
        
        return {
            "status": document_data.get("status", "unknown"),
            "document_id": document_id,
            "document_name": document_data.get("document_name", ""),
            "total_chunks": document_data.get("total_chunks", 0),
            "message": document_data.get("message", ""),
            "upload_timestamp": document_data.get("upload_timestamp", ""),
            "bloom_levels": document_data.get("bloom_levels", []),
            "chunks_per_level": document_data.get("chunks_per_level", 0)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving document status: {str(e)}")