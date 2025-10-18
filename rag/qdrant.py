import time
import uuid
import logging
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, HnswConfigDiff, VectorParams, PointStruct
from langchain_openai import OpenAIEmbeddings
from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List, Union
from core.config import get_settings
from qdrant_client.http import models as rest
from enum import Enum
import os
from core.config import set_redis_json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import re
from typing import List, Dict
from dotenv import load_dotenv
load_dotenv()
from langchain_experimental.text_splitter import SemanticChunker
from langchain_openai.embeddings import OpenAIEmbeddings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# Instantiate the SemanticChunker with an OpenAI embedding model
text_splitter = SemanticChunker(OpenAIEmbeddings())

settings = get_settings()

COLLECTION_NAME = settings.QDRANT_COLLECTION_NAME
VECTOR_SIZE = settings.QDRANT_VECTOR_SIZE
QDRANT_HOST = settings.QDRANT_HOST
QDRANT_PORT = settings.QDRANT_PORT
logging.basicConfig(level=logging.INFO)

class CollectionInfoResponse(BaseModel):
    collection_name: str
    exists: bool
    points_count: Optional[int] = None
    indexed_vectors_count: Optional[int] = None
    status: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    segments_count: Optional[int] = None

class ErrorResponse(BaseModel):
    error: str
    detail: str

class BloomLevel(Enum):
    REMEMBER = "remember"
    UNDERSTAND = "understand"
    APPLY = "apply"
    ANALYZE = "analyze"
    EVALUATE = "evaluate"
    CREATE = "create"

def _get_embedding():
    """Get OpenAI embeddings instance"""
    logging.info("Initializing OpenAI embeddings...")
    return OpenAIEmbeddings(api_key=OPENAI_API_KEY)



def _get_client():
    """Get Qdrant client - minimal working version"""
    logging.info(f"Initializing Qdrant client - Host: {QDRANT_HOST}, Port: {QDRANT_PORT}")
    
    try:
        client = QdrantClient(
            host=QDRANT_HOST,
            port=QDRANT_PORT,
            timeout=60,
            check_compatibility=False
        )
        
        # Just test basic connectivity - DON'T check for specific collections
        # The collection will be created by ensure_collection() if needed
        collections = client.get_collections()
        logging.info(f"Qdrant client initialized successfully. Found {len(collections.collections)} collections.")
        return client
        
    except Exception as e:
        logging.error(f"Failed to initialize Qdrant client: {e}")
        raise HTTPException(status_code=503, detail=f"Qdrant service unavailable: {str(e)}")

def ensure_qdrant_ready():
    """Ensure Qdrant is ready before performing operations"""
    try:
        client = _get_client()
        logging.info("Qdrant connection verified and ready")
        return True
    except Exception as e:
        logging.error(f"Qdrant not ready: {e}")
        raise HTTPException(status_code=503, detail=f"Qdrant service unavailable: {str(e)}")

def ensure_collection(client: QdrantClient, indexing_threshold: int = 10):
    """Create collection if it doesn't exist - handles buggy collection_exists API"""
    start_time = time.time()
    ensure_qdrant_ready()
    logging.info(f"Ensuring collection '{COLLECTION_NAME}' exists...")
    
    try:
        # Check if collection exists (this API is buggy but we'll handle it)
        exists = False
        try:
            exists = client.collection_exists(COLLECTION_NAME)
            logging.info(f"Collection '{COLLECTION_NAME}' exists: {exists}")
        except Exception as e:
            if "404" in str(e) or "Not Found" in str(e):
                exists = False
                logging.info(f"Collection '{COLLECTION_NAME}' does not exist (404 response)")
            else:
                raise
        
        if not exists:
            logging.info(f"Creating collection '{COLLECTION_NAME}'...")
            
            try:
                # Create the collection
                client.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
                    hnsw_config=HnswConfigDiff(m=16, ef_construct=100),
                    on_disk_payload=False,
                    replication_factor=1,
                    shard_number=1,
                )
                logging.info(f"Collection '{COLLECTION_NAME}' created successfully.")
                
            except Exception as create_error:
                # Handle the "already exists" error - this means collection actually exists
                if "already exists" in str(create_error).lower():
                    logging.info(f"Collection '{COLLECTION_NAME}' already exists (detected during creation attempt)")
                else:
                    # Re-raise if it's a different error
                    raise create_error
            
            # Try to update optimizer config
            try:
                client.update_collection(
                    collection_name=COLLECTION_NAME,
                    optimizer_config={"indexing_threshold": indexing_threshold}
                )
                logging.info(f"Optimizer config updated for collection '{COLLECTION_NAME}'")
            except Exception as e:
                logging.warning(f"Could not set optimizer config: {e}")
                
        else:
            logging.info(f"Collection '{COLLECTION_NAME}' already exists.")
        
        # Skip the verification step since collection_exists() is buggy
        # Just assume success if we got here without exceptions
        logging.info(f"Collection '{COLLECTION_NAME}' is ready for use.")
            
    except Exception as e:
        logging.error(f"Error in ensure_collection: {e}")
        logging.error(f"Exception type: {type(e).__name__}")
        import traceback
        logging.error(f"Full traceback: {traceback.format_exc()}")
        raise
    
    elapsed_time = time.time() - start_time
    logging.info(f"ensure_collection completed in {elapsed_time:.2f} seconds.")


def build_chunks(text: str, source: str, doc_type: str = "default"):
    """Split text into chunks using SemanticChunker, with section metadata."""
    
    def detect_section(chunk_text):
        """Simple section detection based on content patterns."""
        lines = chunk_text.strip().split('\n')
        first_line = lines[0].strip()
        
        # Check if first line looks like a header
        if len(first_line) < 100 and (
            first_line.isupper() or
            first_line.startswith('#') or
            (len(lines) > 1 and not first_line.endswith('.') and not first_line.endswith(','))
        ):
            return first_line
        
        # Look for numbered sections
        import re
        section_pattern = r'^(\d+\.?\s+[A-Za-z][^.!?]*)'
        match = re.match(section_pattern, first_line)
        if match:
            return match.group(1)
        
        # Default section name
        return "content"
    
    # Split text into semantically meaningful chunks
    # create_documents returns LangChain Document objects
    langchain_docs = text_splitter.create_documents([text])
    
    # Convert LangChain Documents to your desired format
    docs = []
    for idx, langchain_doc in enumerate(langchain_docs):
        # Access content using page_content attribute
        content = langchain_doc.page_content
        section = detect_section(content)
        
        doc = {
            "content": content,
            "metadata": {
                "source": source,
                "doc_type": doc_type,
                "section": section,
                "chunk_index": idx,
                "chunk_size": len(content),
                "total_chunks": len(langchain_docs),
                # Add more metadata as needed
            }
        }
        docs.append(doc)
    
    return docs



def store_documents(text: str, source: str, document_name: str = None, document_id: str = None):
    """
    Split the text into chunks, embed them, and upsert into Qdrant with Bloom taxonomy support.
    """
    start_time = time.time()
    
    client = _get_client()
    logging.info(f"Storing documents for source '{source}'...")
    ensure_collection(client)

    embedding = _get_embedding()
    chunks = build_chunks(text, source)
    
    if not document_id:
        document_id = str(uuid.uuid4())
    if not document_name:
        document_name = source

    upload_timestamp = datetime.utcnow().isoformat()
    chunk_count = len(chunks)
    
    # Create points for all Bloom levels
    all_points = []
    
    for doc in chunks:
        base_content = doc["content"]
        base_metadata = doc["metadata"].copy()
        
        # Create a point for each Bloom level
        for level in BloomLevel:  # Assuming BloomLevel enum exists
            # Create unique ID for each bloom level version
            pt_id = abs(hash(base_content + source + document_id + level.value + str(doc["metadata"].get("chunk_index", 0))))
            
            # Get embedding (same for all bloom levels of the same content)
            vec = embedding.embed_documents([base_content])[0]
            
            # Create payload with bloom level
            payload = base_metadata.copy()
            payload["content"] = base_content
            payload["document_id"] = document_id
            payload["document_name"] = document_name
            payload["upload_timestamp"] = upload_timestamp
            payload["chunk_count"] = chunk_count
            payload["bloom_level"] = level.value  # This is the key addition!
            
            all_points.append(
                PointStruct(id=pt_id, vector=vec, payload=payload)
            )

    # Store all points
    client.upsert(
        collection_name=COLLECTION_NAME,
        points=all_points,
        wait=True,
    )
    
    logging.info(f"Successfully stored {len(all_points)} document chunks across all Bloom levels.")
    elapsed_time = time.time() - start_time
    logging.info(f"store_documents completed in {elapsed_time:.2f} seconds.")

def get_similar_docs(queries: list[str], k: int = 3, metadata_filter: dict = None) -> str:
    """Perform similarity search for multiple queries and return best-matched context."""
    start_time = time.time()
    client = _get_client()
    embedding = _get_embedding()

    all_results = []
    for query in queries:
        try:
            logging.info(f"Performing similarity search for query: '{query}'...")
            query_vector = embedding.embed_query(query)
            results = client.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vector,
                limit=k,
                with_payload=True,
                score_threshold=0.0
            )
            logging.info(f"Search found {len(results)} results for query: '{query}'")
            if results:
                all_results.append((query, results))
        except Exception as e:
            logging.warning(f"Search error for query '{query}': {e}")

    if not all_results:
        logging.info("No results found for any query.")
        return ""

    # Return results from the query with highest average score
    best_query, best_hits = max(all_results, key=lambda item: sum(hit.score for hit in item[1]) / len(item[1]))
    logging.info(f"Best matching query: '{best_query}'")

    texts = []
    for hit in best_hits:
        logging.info(f"Result score: {hit.score:.4f}")
        content = (
            hit.payload.get('content') or
            hit.payload.get('text') or 
            hit.payload.get('page_content') or 
            str(hit.payload)
        )
        texts.append(content)

    elapsed_time = time.time() - start_time
    logging.info(f"get_similar_docs completed in {elapsed_time:.2f} seconds.")
    return "\n".join(texts)


def reset_index() -> bool:
    """
    Delete the entire collection so you can start fresh.
    """
    start_time = time.time()
   
    client = _get_client()
    logging.info(f"Resetting index for collection '{COLLECTION_NAME}'...")
    if client.collection_exists(COLLECTION_NAME):
        client.delete_collection(COLLECTION_NAME)
        logging.info(f"Collection '{COLLECTION_NAME}' deleted successfully.")
        elapsed_time = time.time() - start_time
        logging.info(f"reset_index completed in {elapsed_time:.2f} seconds.")
        return True
    else:
        logging.info(f"Collection '{COLLECTION_NAME}' does not exist.")
        elapsed_time = time.time() - start_time
        logging.info(f"reset_index completed in {elapsed_time:.2f} seconds.")
        return False

def get_collection_info():
    """Get information about the collection"""
    start_time = time.time()
    
    client = _get_client()
    logging.info(f"Fetching collection info for '{COLLECTION_NAME}'...")
    try:
        if client.collection_exists(COLLECTION_NAME):
            info = client.get_collection(COLLECTION_NAME)
            collection_data = {
                "collection_name": COLLECTION_NAME,
                "exists": True,
                "points_count": info.points_count,
                "indexed_vectors_count": getattr(info, 'indexed_vectors_count', None),
                "status": getattr(info, 'status', None),
                "segments_count": getattr(info, 'segments_count', None),
                "config": {
                    "vector_size": info.config.params.vectors.size,
                    "distance": info.config.params.vectors.distance.value,
                    "shard_number": info.config.params.shard_number,
                    "replication_factor": info.config.params.replication_factor,
                    "on_disk_payload": info.config.params.on_disk_payload,
                    "hnsw_config": {
                        "m": info.config.hnsw_config.m,
                        "ef_construct": info.config.hnsw_config.ef_construct,
                        "full_scan_threshold": info.config.hnsw_config.full_scan_threshold,
                    },
                    "optimizer_config": {
                        "indexing_threshold": info.config.optimizer_config.indexing_threshold,
                        "deleted_threshold": info.config.optimizer_config.deleted_threshold,
                    } if info.config.optimizer_config else None
                }
            }
            elapsed_time = time.time() - start_time
            logging.info(f"get_collection_info completed in {elapsed_time:.2f} seconds.")
            return collection_data
        else:
            elapsed_time = time.time() - start_time
            logging.info(f"get_collection_info completed in {elapsed_time:.2f} seconds.")
            return {
                "collection_name": COLLECTION_NAME,
                "exists": False,
                "points_count": 0,
                "status": "not_found"
            }
    except Exception as e:
        logging.error(f"Failed to get collection info: {e}")
        elapsed_time = time.time() - start_time
        logging.info(f"get_collection_info failed in {elapsed_time:.2f} seconds.")
        raise Exception(f"Failed to get collection info: {str(e)}")
def store_document_chunks(chunks: List[str], metadata: List[Dict[str, Any]], source: str = None):
    """
    Store pre-processed chunks with metadata directly into Qdrant.
    """

    if not chunks:
        logging.warning("No chunks provided to store")
        return 0
    
    client = _get_client()
    ensure_collection(client)
    embedding = _get_embedding()
    points = []
    
    logging.info(f"Processing {len(chunks)} chunks for storage")
    
    # Optimize: Get all embeddings in a single API call
    logging.info(f"Generating embeddings for {len(chunks)} chunks in batch...")
    all_embeddings = embedding.embed_documents(chunks)
    logging.info(f"Generated {len(all_embeddings)} embeddings successfully")
    
    # Handle case where metadata list is shorter than chunks list
    for i, (chunk, vec) in enumerate(zip(chunks, all_embeddings)):
        try:
            # Use the first metadata entry for all chunks if metadata list is shorter
            if i < len(metadata):
                chunk_metadata = metadata[i]
            else:
                chunk_metadata = metadata[0].copy() if metadata else {}
            
            chunk_id_str = f"{chunk}_{source}_{i}" if source else f"{chunk}_{i}"
            pt_id = abs(hash(chunk_id_str))
            
         
            payload = chunk_metadata.copy()
            payload["content"] = chunk
            
            
            if source:
                payload["source"] = source
            
            
            payload["chunk_index"] = i
            
            points.append(
                PointStruct(id=pt_id, vector=vec, payload=payload)
            )
            
        except Exception as e:
            logging.error(f"Error processing chunk {i}: {str(e)}")
            continue
    
    if not points:
        logging.error("No valid points created from chunks")
        return 0
    
    try:
       
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
            wait=True,
        )
        
        logging.info(f"Successfully stored {len(points)} document chunks")
        return len(points)
        
    except Exception as e:
        logging.error(f"Error storing chunks in Qdrant: {str(e)}")
        raise


def store_documents_with_custom_chunking(
    chunks: List[str], 
    metadata: Union[Dict[str, Any], List[Dict[str, Any]]], 
    source: str = None
):
    """
    Alternative version with more flexible metadata handling.
    
    Args:
        chunks: List of text chunks
        metadata: Either a single metadata dict for all chunks, or list of metadata dicts
        source: Optional source identifier
    
    Returns:
        int: Number of chunks stored
    """
    if not chunks:
        return 0
    
    
    if isinstance(metadata, dict):
        metadata_list = [metadata.copy() for _ in chunks]
    else:
        metadata_list = metadata
        if len(chunks) != len(metadata_list):
            raise ValueError("Number of chunks must match number of metadata entries")
    
    return store_document_chunks(chunks, metadata_list, source)

def delete_document_by_id(document_id: str) -> dict:
    """Delete all chunks belonging to a specific document ID"""
    client = _get_client()
    try:
        search_result = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=rest.Filter(
                must=[
                    rest.FieldCondition(
                        key="document_id",
                        match=rest.MatchValue(value=document_id)
                    )
                ]
            ),
            limit=10000
        )
        point_ids = [point.id for point in search_result[0]]
        if point_ids:
            client.delete(
                collection_name=COLLECTION_NAME,
                points_selector=rest.PointIdsList(points=point_ids)
            )
            return {
                "status": "success",
                "document_id": document_id,
                "chunks_deleted": len(point_ids),
                "message": f"Deleted {len(point_ids)} chunks for document ID: {document_id}"
            }
        else:
            return {
                "status": "not_found",
                "document_id": document_id,
                "chunks_deleted": 0,
                "message": f"No chunks found for document ID: {document_id}"
            }
    except Exception as e:
        return {
            "status": "error",
            "document_id": document_id,
            "chunks_deleted": 0,
            "message": f"Error deleting document: {e}"
        }

def get_document_chunks_by_id(document_id: str) -> list:
    """Get all chunks for a specific document ID"""
    client = _get_client()
    try:
        search_result = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=rest.Filter(
                must=[
                    rest.FieldCondition(
                        key="document_id",
                        match=rest.MatchValue(value=document_id)
                    )
                ]
            ),
            limit=10000
        )
        chunks = []
        for point in search_result[0]:
            chunks.append({
                "point_id": point.id,
                "chunk_index": point.payload.get("chunk_index"),
                "content": point.payload.get("content"),
                "metadata": point.payload
            })
        chunks.sort(key=lambda x: x.get("chunk_index", 0))
        return chunks
    except Exception as e:
        return []

def list_all_documents() -> list:
    """List all documents in the collection"""
    client = _get_client()
    try:
        search_result = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=10000
        )
        documents = {}
        for point in search_result[0]:
            doc_id = point.payload.get("document_id")
            doc_name = point.payload.get("document_name")
            upload_timestamp = point.payload.get("upload_timestamp")
            if doc_id and doc_id not in documents:
                documents[doc_id] = {
                    "document_id": doc_id,
                    "document_name": doc_name,
                    "upload_timestamp": upload_timestamp,
                    "chunk_count": 0
                }
            if doc_id:
                documents[doc_id]["chunk_count"] += 1
        return list(documents.values())
    except Exception as e:
        return []

def store_documents_with_id(text: str, source: str, document_name: str, document_id: str = None):
    """
    Store documents with a specific document ID for tracking and management.
    
    Args:
        text: The document text to store
        source: Source identifier
        document_name: Human-readable name for the document
        document_id: Unique identifier for the document (auto-generated if None)
    
    Returns:
        dict: Result with status, document_id, document_name, total_chunks, and message
    """
    import uuid
    from datetime import datetime
    
    start_time = time.time()
    
    # Generate document_id if not provided
    if not document_id:
        document_id = str(uuid.uuid4())
    
    client = _get_client()
    logging.info(f"Storing document '{document_name}' with ID '{document_id}'...")
    ensure_collection(client)

    embedding = _get_embedding()
    chunks = build_chunks(text, source)
    points = []
    
    upload_timestamp = datetime.now().isoformat()
    
    for i, doc in enumerate(chunks):
        try:
            # Create unique point ID
            pt_id = abs(hash(doc["content"] + source + document_id + str(i)))
            
            # Generate embedding
            vec = embedding.embed_documents([doc["content"]])[0]
            
            # Prepare payload with document metadata
            payload = doc["metadata"].copy()
            payload["content"] = doc["content"]
            payload["document_id"] = document_id
            payload["document_name"] = document_name
            payload["upload_timestamp"] = upload_timestamp
            payload["chunk_index"] = i
            payload["total_chunks"] = len(chunks)
            
            points.append(
                PointStruct(id=pt_id, vector=vec, payload=payload)
            )
            
        except Exception as e:
            logging.error(f"Error processing chunk {i}: {str(e)}")
            continue
    
    if not points:
        logging.error("No valid points created from document")
        return {
            "status": "error",
            "document_id": document_id,
            "document_name": document_name,
            "total_chunks": 0,
            "message": "Failed to process document chunks"
        }
    
    try:
        # Store in Qdrant
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
            wait=True,
        )
        
        elapsed_time = time.time() - start_time
        logging.info(f"Successfully stored document '{document_name}' with {len(points)} chunks in {elapsed_time:.2f} seconds.")
        
        return {
            "status": "success",
            "document_id": document_id,
            "document_name": document_name,
            "total_chunks": len(points),
            "message": f"Document stored successfully with {len(points)} chunks"
        }
        
    except Exception as e:
        logging.error(f"Error storing document in Qdrant: {str(e)}")
        return {
            "status": "error",
            "document_id": document_id,
            "document_name": document_name,
            "total_chunks": 0,
            "message": f"Error storing document: {str(e)}"
        }

def store_document_bloom_parallel(text: str, source: str, document_name: str, document_id: str = None):
    """
    Store the document for all Bloom taxonomy levels using parallel processing with batching.
    This prevents timeout issues by processing data in smaller batches.
    """
    from datetime import datetime
    import uuid
    logging.info(f"document_id ------ {document_id}")
    if not document_id:
        document_id = str(uuid.uuid4())

    client = _get_client()
    ensure_collection(client)
    embedding = _get_embedding()
    chunks = build_chunks(text, source)
    upload_timestamp = datetime.now().isoformat()
    redis_key = f"document_{document_id}"

    def process_bloom_level(level):
        """Process a single Bloom level - this runs in parallel"""
        level_points = []
        try:
            for i, doc in enumerate(chunks):
                pt_id = abs(hash(doc["content"] + source + document_id + level.value + str(i)))
                vec = embedding.embed_documents([doc["content"]])[0]
                
                payload = doc["metadata"].copy()
                payload["content"] = doc["content"]
                payload["document_id"] = document_id
                payload["document_name"] = document_name
                payload["upload_timestamp"] = upload_timestamp
                payload["chunk_index"] = i
                payload["total_chunks"] = len(chunks)
                payload["bloom_level"] = level.value
                
                level_points.append(
                    PointStruct(id=pt_id, vector=vec, payload=payload)
                )
            
            logging.info(f"Processed {len(level_points)} chunks for Bloom level: {level.value}")
            return level_points
            
        except Exception as e:
            logging.error(f"Error processing Bloom level {level.value}: {str(e)}")
            return []

    # Process all Bloom levels in parallel
    logging.info(f"Starting parallel processing for {len(BloomLevel)} Bloom levels")
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_to_level = {executor.submit(process_bloom_level, level): level for level in BloomLevel}
        
        all_points = []
        for future in future_to_level:
            try:
                level_points = future.result()
                all_points.extend(level_points)
            except Exception as e:
                level = future_to_level[future]
                logging.error(f"Failed to process Bloom level {level.value}: {str(e)}")

    processing_time = time.time() - start_time
    logging.info(f"Parallel processing completed in {processing_time:.2f} seconds")

    if not all_points:
        logging.error("No valid points created from document")
        er_payload =  {
            "status": "error",
            "document_id": document_id,
            "document_name": document_name,
            "total_chunks": 0,
            "message": "Failed to process document chunks",
            "processing_time": processing_time
        }

        set_redis_json(redis_key, er_payload)
        return er_payload
    try:
        # BATCHING: Process points in smaller batches to prevent timeouts
        batch_size = 100  # Process 100 points at a time
        total_batches = (len(all_points) + batch_size - 1) // batch_size
        
        logging.info(f"Storing {len(all_points)} points in {total_batches} batches of {batch_size}...")
        storage_start_time = time.time()
        
        successful_batches = 0
        for i in range(0, len(all_points), batch_size):
            batch = all_points[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            batch_start_time = time.time()
            
            logging.info(f"Storing batch {batch_num}/{total_batches} ({len(batch)} points)...")
            
            try:
                client.upsert(
            collection_name=COLLECTION_NAME,
                    points=batch,
                    wait=True,
                )
                batch_time = time.time() - batch_start_time
                logging.info(f"Batch {batch_num} stored successfully in {batch_time:.2f} seconds")
                successful_batches += 1
                
            except Exception as batch_error:
                logging.error(f"Error storing batch {batch_num}: {str(batch_error)}")
                # Continue with next batch instead of failing completely
                continue
        
        storage_time = time.time() - storage_start_time
        total_time = time.time() - start_time
        
        if successful_batches == total_batches:
            logging.info(f"Successfully stored all {total_batches} batches")
            success_payload = {
                "status": "success",
                "document_id": document_id,
                "document_name": document_name,
                "total_chunks": len(all_points),
                "bloom_levels_processed": len(BloomLevel),
                "processing_time": processing_time,
                "storage_time": storage_time,
                "total_time": total_time,
                "batches_processed": successful_batches,
                "total_batches": total_batches,
                "message": f"Document stored for all Bloom levels with {len(all_points)} chunks (parallel processing + batching)"
            }

            set_redis_json(redis_key, success_payload)
            return success_payload
        else:
            logging.warning(f"Partially successful: {successful_batches}/{total_batches} batches stored")
            partial_success_payload =  {
                "status": "partial_success",
                "document_id": document_id,
                "document_name": document_name,
                "total_chunks": len(all_points),
                "bloom_levels_processed": len(BloomLevel),
                "processing_time": processing_time,
                "storage_time": storage_time,
                "total_time": total_time,
                "batches_processed": successful_batches,
                "total_batches": total_batches,
                "message": f"Partially stored: {successful_batches}/{total_batches} batches successful"
            }
            set_redis_json(redis_key, partial_success_payload)
            return partial_success_payload
    except Exception as e:
        logging.error(f"Error in storage process: {str(e)}")
        error_payload =  {
            "status": "error",
            "document_id": document_id,
            "document_name": document_name,
            "total_chunks": 0,
            "processing_time": processing_time,
            "message": f"Error storing document: {str(e)}"
        }
        set_redis_json(redis_key, error_payload)
        return error_payload