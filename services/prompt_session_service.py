
from datetime import datetime
from typing import Optional, List, Dict, Any
from pymongo import MongoClient
import os
from bson.objectid import ObjectId

class PromptSessionService:
    def __init__(self):
        self.mongo_client = MongoClient(os.getenv("MONGO_URI"))
        self.db = self.mongo_client.get_database()
        self.collection = self.db["prompt_sessions"]

    def save_prompt(self, org_id: str, user_id: str, prompt_text: str, intent: str, prompt_name: Optional[str] = None, metadata: Dict = None) -> Dict[str, Any]:
        """
        Save a generated prompt to the database.
        If no name is provided, generates a default name based on intent and timestamp.
        """
        timestamp = datetime.utcnow()
        
        if not prompt_name:
            # Auto-generate name if not provided: "Intent_YYYYMMDD_HHMM"
            date_str = timestamp.strftime("%Y%m%d_%H%M")
            prompt_name = f"{intent}_{date_str}"

        doc = {
            "org_id": org_id,
            "user_id": user_id,
            "prompt_name": prompt_name,
            "intent": intent,
            "prompt_text": prompt_text,
            "metadata": metadata or {},
            "created_at": timestamp
        }

        # Check if name exists for this user/org to avoid duplicates (optional, or specific logic)
        # For now, we will perform an upsert based on name if provided, or just insert.
        # However, to keep history of same name, we might just insert. 
        # Requirement: "retrive latest timestamp" -> standard insert is best.
        
        result = self.collection.insert_one(doc)
        doc["_id"] = str(result.inserted_id)
        return doc

    def get_latest_prompt(self, org_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve the most recently created prompt for the user.
        """
        result = self.collection.find_one(
            {"org_id": org_id, "user_id": user_id},
            sort=[("created_at", -1)]
        )
        if result:
            result["_id"] = str(result["_id"])
        return result

    def get_prompt_by_name(self, org_id: str, user_id: str, prompt_name: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific prompt by its name for the user.
        Gets the latest version if multiple exist with the same name.
        """
        result = self.collection.find_one(
            {"org_id": org_id, "user_id": user_id, "prompt_name": prompt_name},
            sort=[("created_at", -1)]
        )
        if result:
            result["_id"] = str(result["_id"])
        return result

    def get_prompt_by_hash(self, org_id: str, user_id: str, content_hash: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a prompt via its content hash for caching.
        """
        result = self.collection.find_one(
            {"org_id": org_id, "user_id": user_id, "metadata.content_hash": content_hash},
            sort=[("created_at", -1)]
        )
        if result:
            result["_id"] = str(result["_id"])
        return result

    def list_prompts(self, org_id: str, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        List available prompts for the user, sorted by newest first.
        """
        cursor = self.collection.find(
            {"org_id": org_id, "user_id": user_id},
            {"prompt_text": 0} # Exclude full text for list view to save bandwidth
        ).sort("created_at", -1).limit(limit)

        results = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            results.append(doc)
        return results
