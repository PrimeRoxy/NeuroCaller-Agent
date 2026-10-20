"""
Campaign Tracking Service

Manages campaign lifecycle and status tracking in MongoDB and Redis.
Provides real-time progress tracking for agentic dashboards.
"""

import json
from datetime import datetime
from typing import Dict, Any, List, Optional
from pymongo import MongoClient
from core.config import redis_client
import os
from dotenv import load_dotenv

load_dotenv()

# MongoDB connection
mongo_client = MongoClient(os.getenv("MONGO_URI"))
db = mongo_client.get_database()
campaigns_collection = db["campaigns"]


# ============================================================================
# REDIS KEY HELPERS
# ============================================================================

def campaign_keys(campaign_id: str) -> Dict[str, str]:
    """Get Redis key names for a campaign."""
    return {
        "status": f"campaign:{campaign_id}:status",
        "progress": f"campaign:{campaign_id}:progress",
        "events": f"campaign:{campaign_id}:events",  # Pub/sub channel
    }


# ============================================================================
# CAMPAIGN STATUS ENUM
# ============================================================================

class CampaignStatus:
    CREATED = "created"
    VALIDATING = "validating"
    GENERATING_PROMPT = "generating_prompt"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ============================================================================
# CAMPAIGN TRACKING SERVICE
# ============================================================================

class CampaignTrackingService:
    """
    Service for tracking campaign lifecycle and progress.
    
    Uses:
    - MongoDB for persistent campaign records
    - Redis for real-time status and progress
    """
    
    def __init__(self):
        self._ensure_indexes()
    
    def _ensure_indexes(self):
        """Create MongoDB indexes for efficient queries."""
        try:
            campaigns_collection.create_index([("org_id", 1), ("user_id", 1)])
            campaigns_collection.create_index([("campaign_id", 1)], unique=True)
            campaigns_collection.create_index([("status", 1)])
            campaigns_collection.create_index([("created_at", -1)])
        except Exception as e:
            print(f"Warning: Could not create indexes: {e}")
    
    # ========================================================================
    # CAMPAIGN LIFECYCLE
    # ========================================================================

    def create_campaign(
        self,
        campaign_id: str,
        org_id: str,
        user_id: str,
        total_numbers: int,
        agent_number: str = None,
        prompt_source: str = None
    ) -> Dict[str, Any]:
        """
        Create a new campaign record.
        
        Args:
            campaign_id: Unique campaign identifier (org:user:timestamp)
            org_id: Organization ID
            user_id: User ID
            total_numbers: Total phone numbers in campaign
            plivo_number: Plivo number for outbound calls
            prompt_source: 'generated' or 'cached'
            
        Returns:
            Created campaign document
        """
        now = datetime.utcnow()
        
        campaign = {
            "campaign_id": campaign_id,
            "org_id": org_id,
            "user_id": user_id,
            "status": CampaignStatus.CREATED,
            "total_numbers": total_numbers,
            "completed": 0,
            "failed": 0,
            "pending": total_numbers,
            "in_progress": 0,
            "callbacks_scheduled": 0,
            "callbacks_completed": 0,
            "plivo_number": agent_number,
            "prompt_source": prompt_source,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "updated_at": now
        }
        
        # Store in MongoDB
        try:
            campaigns_collection.insert_one(campaign)
        except Exception as e:
            # Handle duplicate key error
            if "duplicate key" in str(e).lower():
                campaigns_collection.update_one(
                    {"campaign_id": campaign_id},
                    {"$set": campaign}
                )
            else:
                raise
        
        # Initialize Redis tracking
        keys = campaign_keys(campaign_id)
        redis_client.setex(keys["status"], 86400 * 7, CampaignStatus.CREATED)  # 7 days TTL
        redis_client.setex(
            keys["progress"],
            86400 * 7,
            json.dumps({
                "total": total_numbers,
                "completed": 0,
                "failed": 0,
                "pending": total_numbers,
                "in_progress": 0,
                "callbacks_scheduled": 0,
                "callbacks_completed": 0
            })
        )
        
        return campaign

    def update_status(
        self,
        campaign_id: str,
        status: str,
        error_message: str = None
    ) -> bool:
        """
        Update campaign status.
        
        Args:
            campaign_id: Campaign identifier
            status: New status (use CampaignStatus constants)
            error_message: Optional error message for failed status
            
        Returns:
            True if updated successfully
        """
        now = datetime.utcnow()
        
        update_data = {
            "status": status,
            "updated_at": now
        }
        
        if status == CampaignStatus.RUNNING:
            update_data["started_at"] = now
        elif status in [CampaignStatus.COMPLETED, CampaignStatus.FAILED]:
            update_data["completed_at"] = now
        
        if error_message:
            update_data["error_message"] = error_message
        
        # Update MongoDB
        result = campaigns_collection.update_one(
            {"campaign_id": campaign_id},
            {"$set": update_data}
        )
        
        # Update Redis
        keys = campaign_keys(campaign_id)
        redis_client.setex(keys["status"], 86400 * 7, status)
        
        return result.modified_count > 0
    
    def update_progress(
        self,
        campaign_id: str,
        completed: int = None,
        failed: int = None,
        pending: int = None,
        in_progress: int = None,
        callbacks_scheduled: int = None,
        callbacks_completed: int = None
    ) -> Dict[str, Any]:
        """
        Update campaign progress counters.
        
        Args:
            campaign_id: Campaign identifier
            completed: Number of completed calls
            failed: Number of failed calls
            pending: Number of pending calls
            in_progress: Number of calls currently in progress
            callbacks_scheduled: Number of callbacks scheduled
            callbacks_completed: Number of callbacks completed
            
        Returns:
            Updated progress dict
        """
        keys = campaign_keys(campaign_id)
        
        # Get current progress from Redis
        progress_raw = redis_client.get(keys["progress"])
        if progress_raw:
            progress = json.loads(progress_raw)
        else:
            progress = {
                "total": 0, "completed": 0, "failed": 0, "pending": 0, "in_progress": 0,
                "callbacks_scheduled": 0, "callbacks_completed": 0
            }
        
        # Update only provided values
        if completed is not None:
            progress["completed"] = completed
        if failed is not None:
            progress["failed"] = failed
        if pending is not None:
            progress["pending"] = pending
        if in_progress is not None:
            progress["in_progress"] = in_progress
        if callbacks_scheduled is not None:
            progress["callbacks_scheduled"] = callbacks_scheduled
        if callbacks_completed is not None:
            progress["callbacks_completed"] = callbacks_completed
        
        # Save to Redis
        redis_client.setex(keys["progress"], 86400 * 7, json.dumps(progress))
        
        # Update MongoDB periodically (not every call to reduce writes)
        campaigns_collection.update_one(
            {"campaign_id": campaign_id},
            {"$set": {
                "completed": progress["completed"],
                "failed": progress["failed"],
                "pending": progress["pending"],
                "in_progress": progress["in_progress"],
                "callbacks_scheduled": progress.get("callbacks_scheduled", 0),
                "callbacks_completed": progress.get("callbacks_completed", 0),
                "updated_at": datetime.utcnow()
            }}
        )
        
        return progress

    def increment_progress(
        self,
        campaign_id: str,
        completed_delta: int = 0,
        failed_delta: int = 0,
        in_progress_delta: int = 0,
        total_delta: int = 0,
        callbacks_scheduled_delta: int = 0,
        callbacks_completed_delta: int = 0
    ) -> Dict[str, Any]:
        """
        Increment progress counters atomically.
        
        Args:
            campaign_id: Campaign identifier
            completed_delta: Amount to add to completed count
            failed_delta: Amount to add to failed count
            in_progress_delta: Amount to add/subtract from in_progress count
            total_delta: Amount to add to the campaign total (e.g. a callback adds a new call)
            callbacks_scheduled_delta: Amount to add to scheduled callbacks count
            callbacks_completed_delta: Amount to add to completed callbacks count

        Returns:
            Updated progress dict
        """
        keys = campaign_keys(campaign_id)
        
        # Get current progress
        progress_raw = redis_client.get(keys["progress"])
        if progress_raw:
            progress = json.loads(progress_raw)
        else:
            return {
                "total": 0, "completed": 0, "failed": 0, "pending": 0, "in_progress": 0,
                "callbacks_scheduled": 0, "callbacks_completed": 0
            }
        
        # Update counters
        progress["completed"] += completed_delta
        progress["failed"] += failed_delta
        progress["in_progress"] += in_progress_delta
        progress["total"] = progress.get("total", 0) + total_delta
        progress["callbacks_scheduled"] = max(0, progress.get("callbacks_scheduled", 0) + callbacks_scheduled_delta)
        progress["callbacks_completed"] = max(0, progress.get("callbacks_completed", 0) + callbacks_completed_delta)
        progress["pending"] = max(0, progress["total"] - progress["completed"] - progress["failed"] - progress["in_progress"])

        # Save to Redis
        redis_client.setex(keys["progress"], 86400 * 7, json.dumps(progress))

        # Sync to MongoDB
        mongo_set = {
            "completed": progress["completed"],
            "failed": progress["failed"],
            "pending": progress["pending"],
            "in_progress": progress["in_progress"],
            "callbacks_scheduled": progress["callbacks_scheduled"],
            "callbacks_completed": progress["callbacks_completed"],
            "updated_at": datetime.utcnow()
        }
        # Keep the displayed denominator (total_numbers) in sync when total grows
        if total_delta:
            mongo_set["total_numbers"] = progress["total"]
        campaigns_collection.update_one(
            {"campaign_id": campaign_id},
            {"$set": mongo_set}
        )
        
        return progress
    
    # ========================================================================
    # CAMPAIGN QUERIES
    # ========================================================================

    def get_campaign(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        """
        Get campaign details with real-time progress.
        
        Args:
            campaign_id: Campaign identifier
            
        Returns:
            Campaign document with real-time progress, or None
        """
        # Get from MongoDB
        campaign = campaigns_collection.find_one(
            {"campaign_id": campaign_id},
            {"_id": 0}
        )
        
        if not campaign:
            return None
        
        # Enrich with real-time progress from Redis
        keys = campaign_keys(campaign_id)
        
        status = redis_client.get(keys["status"])
        if status:
            campaign["status"] = status
        
        progress_raw = redis_client.get(keys["progress"])
        if progress_raw:
            progress = json.loads(progress_raw)
            campaign.update(progress)
        
        # Convert datetime to string for JSON serialization
        for key in ["created_at", "started_at", "completed_at", "updated_at"]:
            if campaign.get(key) and isinstance(campaign[key], datetime):
                campaign[key] = campaign[key].isoformat()
        
        return campaign
    
    def get_progress(self, campaign_id: str) -> Dict[str, Any]:
        """
        Get real-time campaign progress from Redis.
        
        Args:
            campaign_id: Campaign identifier
            
        Returns:
            Progress dict with status
        """
        keys = campaign_keys(campaign_id)
        
        status = redis_client.get(keys["status"]) or "unknown"
        progress_raw = redis_client.get(keys["progress"])
        
        if progress_raw:
            progress = json.loads(progress_raw)
        else:
            progress = {
                "total": 0, "completed": 0, "failed": 0, "pending": 0, "in_progress": 0,
                "callbacks_scheduled": 0, "callbacks_completed": 0
            }
        
        progress["status"] = status
        progress["campaign_id"] = campaign_id
        progress["callbacks_scheduled"] = progress.get("callbacks_scheduled", 0)
        progress["callbacks_completed"] = progress.get("callbacks_completed", 0)
        progress["callbacks_pending"] = max(0, progress["callbacks_scheduled"] - progress["callbacks_completed"])
        
        return progress
    
    def list_campaigns(
        self,
        org_id: str,
        user_id: Optional[str] = None,
        status: str = None,
        limit: int = 20,
        skip: int = 0
    ) -> List[Dict[str, Any]]:
        """
        List campaigns for an organization/user.
        
        Args:
            org_id: Organization ID
            user_id: Optional User ID. If None, returns all campaigns for the org.
            status: Optional status filter
            limit: Maximum results
            skip: Results to skip (pagination)
            
        Returns:
            List of campaign documents
        """
        query = {"org_id": org_id}
        if user_id:
            query["user_id"] = user_id
            
        if status:
            query["status"] = status
        
        campaigns = list(
            campaigns_collection.find(query, {"_id": 0})
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        
        # Enrich with real-time status and progress from Redis
        for campaign in campaigns:
            campaign_id = campaign.get("campaign_id")
            if campaign_id:
                keys = campaign_keys(campaign_id)
                redis_status = redis_client.get(keys["status"])
                if redis_status:
                    campaign["status"] = redis_status
                
                # Enrich with real-time progress counters from Redis
                progress_raw = redis_client.get(keys["progress"])
                if progress_raw:
                    try:
                        progress = json.loads(progress_raw)
                        campaign.update(progress)
                    except (json.JSONDecodeError, TypeError):
                        pass
            
            # Convert datetime for JSON
            for key in ["created_at", "started_at", "completed_at", "updated_at"]:
                if campaign.get(key) and isinstance(campaign[key], datetime):
                    campaign[key] = campaign[key].isoformat()
        
        return campaigns
    
    def get_stats_overview(
        self,
        org_id: str,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get aggregate statistics for dashboard overview.
        
        Args:
            org_id: Organization ID
            user_id: Optional User ID to filter by
            
        Returns:
            Statistics summary
        """
        match_query = {"org_id": org_id}
        if user_id:
            match_query["user_id"] = user_id
            
        pipeline = [
            {"$match": match_query},
            {"$group": {
                "_id": None,
                "total_campaigns": {"$sum": 1},
                "total_calls": {"$sum": "$total_numbers"},
                "completed_calls": {"$sum": "$completed"},
                "failed_calls": {"$sum": "$failed"},
                "active_campaigns": {
                    "$sum": {"$cond": [{"$eq": ["$status", "running"]}, 1, 0]}
                }
            }}
        ]
        
        result = list(campaigns_collection.aggregate(pipeline))
        
        if result:
            stats = result[0]
            del stats["_id"]
            
            # Calculate success rate
            total = stats.get("completed_calls", 0) + stats.get("failed_calls", 0)
            if total > 0:
                stats["success_rate"] = round(stats["completed_calls"] / total * 100, 2)
            else:
                stats["success_rate"] = 0
        else:
            stats = {
                "total_campaigns": 0,
                "total_calls": 0,
                "completed_calls": 0,
                "failed_calls": 0,
                "active_campaigns": 0,
                "success_rate": 0
            }
        
        return stats


# Singleton instance
_tracking_service: Optional[CampaignTrackingService] = None


def get_campaign_tracking_service() -> CampaignTrackingService:
    """Get or create singleton instance."""
    global _tracking_service
    if _tracking_service is None:
        _tracking_service = CampaignTrackingService()
    return _tracking_service
