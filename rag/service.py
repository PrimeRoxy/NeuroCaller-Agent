import logging
from dotenv import load_dotenv
import os
from qdrant_client import QdrantClient
from rag.qdrant import _get_embedding
from dotenv import load_dotenv
from openai import OpenAI
load_dotenv()

client_openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
try:
    qdrant_client = QdrantClient(
        url=f"http://{QDRANT_HOST}:6333"
    )
    COLLECTION_NAME = "salesagent_collection"
    qdrant_client.get_collection(collection_name=COLLECTION_NAME)
    print("Successfully connected to Qdrant collection:", COLLECTION_NAME)
except Exception as e:
    print(f"Error connecting to Qdrant: {e}")
    qdrant_client = None
embedding = _get_embedding()


def rag_search_impl(query: str, top_k: int = 3, score_threshold: float = 0.5) -> dict:
    """
    Very simple RAG:
      1) Embeds the (English) query with OpenAI.
      2) Searches Qdrant.
      3) Returns JSON { hits: [...], context: "..." }
    """
    if not query or not isinstance(query, str):
        return { "context": "" }
    query_vector = embedding.embed_query(query)

    # 2) Search
    results = qdrant_client.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        limit=top_k,
        score_threshold=score_threshold,
    )
    logging.info(f"Qdrant search results: {len(results)} found")

    # 3) Collect
    hits, chunks = [], []
    for p in results:
        payload = p.payload or {}
        text = (payload.get("content") or "").strip()
        print(text)
        if not text:
            continue
        hits.append({"id": str(p.id), "score": float(p.score), "text": text})
        chunks.append(text)

    return {"context": "\n---\n".join(chunks)}
