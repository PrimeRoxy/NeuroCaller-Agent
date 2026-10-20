"""
Task Event Service

Tracks agent activities and emits events for real-time monitoring.
Uses Redis pub/sub for event distribution and MongoDB for persistence.
"""

import json
from datetime import datetime
from typing import Dict, Any, List, Optional, Generator
from pymongo import MongoClient
from core.config import redis_client
import os
from dotenv import load_dotenv
import asyncio

load_dotenv()

# MongoDB connection
mongo_client = MongoClient(os.getenv("MONGO_URI"))
db = mongo_client.get_database()
events_collection = db["campaign_events"]


# ============================================================================
# EVENT TYPES
# ============================================================================

class EventType:
    # Campaign lifecycle
    CAMPAIGN_CREATED = "CAMPAIGN_CREATED"
    CAMPAIGN_STARTED = "CAMPAIGN_STARTED"
    CAMPAIGN_PAUSED = "CAMPAIGN_PAUSED"
    CAMPAIGN_RESUMED = "CAMPAIGN_RESUMED"
    CAMPAIGN_COMPLETED = "CAMPAIGN_COMPLETED"
    CAMPAIGN_FAILED = "CAMPAIGN_FAILED"
    
    # Sub-agent activities
    VALIDATION_STARTED = "VALIDATION_STARTED"
    VALIDATION_COMPLETED = "VALIDATION_COMPLETED"
    PROMPT_GENERATION_STARTED = "PROMPT_GENERATION_STARTED"
    PROMPT_GENERATION_COMPLETED = "PROMPT_GENERATION_COMPLETED"
    
    # Call lifecycle
    CALL_QUEUED = "CALL_QUEUED"
    CALL_STARTED = "CALL_STARTED"
    CALL_ANSWERED = "CALL_ANSWERED"
    CALL_COMPLETED = "CALL_COMPLETED"
    CALL_FAILED = "CALL_FAILED"
    
    # Progress updates
    PROGRESS_UPDATE = "PROGRESS_UPDATE"


# ============================================================================
# TASK EVENT SERVICE
# ============================================================================

class TaskEventService:
    """
    Service for emitting and subscribing to task events.
    
    Uses:
    - Redis pub/sub for real-time event distribution
    - MongoDB for event persistence (last 7 days)
    """
    
    def __init__(self):
        self._pubsub = None
        self._ensure_indexes()
    
    def _ensure_indexes(self):
        """Create MongoDB indexes."""
        try:
            events_collection.create_index([("campaign_id", 1), ("timestamp", -1)])
            events_collection.create_index([("event_type", 1)])
            events_collection.create_index(
                [("timestamp", 1)],
                expireAfterSeconds=86400 * 7  # Auto-delete after 7 days
            )
        except Exception as e:
            print(f"Warning: Could not create event indexes: {e}")
    
    # ========================================================================
    # EVENT EMISSION
    # ========================================================================
    
    def emit(
        self,
        event_type: str,
        campaign_id: str,
        data: Dict[str, Any] = None,
        org_id: str = None,
        user_id: str = None
    ) -> Dict[str, Any]:
        """
        Emit an event to Redis pub/sub and store in MongoDB.
        
        Args:
            event_type: Type of event (use EventType constants)
            campaign_id: Campaign identifier
            data: Event-specific data
            org_id: Organization ID
            user_id: User ID
            
        Returns:
            The emitted event
        """
        event = {
            "event_type": event_type,
            "campaign_id": campaign_id,
            "org_id": org_id,
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data or {}
        }
        
        # Store in MongoDB
        try:
            event_doc = event.copy()
            event_doc["timestamp"] = datetime.utcnow()  # Use datetime for TTL index
            events_collection.insert_one(event_doc)
        except Exception as e:
            print(f"Warning: Failed to store event: {e}")
        
        # Publish to Redis
        channel = f"campaign:{campaign_id}:events"
        try:
            redis_client.publish(channel, json.dumps(event))
        except Exception as e:
            print(f"Warning: Failed to publish event: {e}")
        
        return event
    
    def emit_campaign_created(
        self,
        campaign_id: str,
        org_id: str,
        user_id: str,
        total_numbers: int,
    ):
        """Emit campaign created event."""
        return self.emit(
            EventType.CAMPAIGN_CREATED,
            campaign_id,
            {
                "total_numbers": total_numbers
            },
            org_id,
            user_id
        )
    
    def emit_campaign_started(self, campaign_id: str, org_id: str = None, user_id: str = None):
        """Emit campaign started event."""
        return self.emit(EventType.CAMPAIGN_STARTED, campaign_id, {}, org_id, user_id)
    
    def emit_campaign_completed(
        self,
        campaign_id: str,
        completed: int,
        failed: int,
        org_id: str = None,
        user_id: str = None
    ):
        """Emit campaign completed event."""
        return self.emit(
            EventType.CAMPAIGN_COMPLETED,
            campaign_id,
            {"completed": completed, "failed": failed},
            org_id,
            user_id
        )
    
    def emit_campaign_failed(
        self,
        campaign_id: str,
        error: str,
        org_id: str = None,
        user_id: str = None
    ):
        """Emit campaign failed event."""
        return self.emit(
            EventType.CAMPAIGN_FAILED,
            campaign_id,
            {"error": error},
            org_id,
            user_id
        )
    
    def emit_validation_started(self, campaign_id: str, count: int):
        """Emit validation started event."""
        return self.emit(
            EventType.VALIDATION_STARTED,
            campaign_id,
            {"numbers_to_validate": count}
        )
    
    def emit_validation_completed(
        self,
        campaign_id: str,
        valid: int,
        invalid: int,
        duplicates: int
    ):
        """Emit validation completed event."""
        return self.emit(
            EventType.VALIDATION_COMPLETED,
            campaign_id,
            {
                "valid_numbers": valid,
                "invalid_numbers": invalid,
                "duplicates_removed": duplicates
            }
        )
    
    def emit_prompt_generation_started(self, campaign_id: str, intent: str):
        """Emit prompt generation started event."""
        return self.emit(
            EventType.PROMPT_GENERATION_STARTED,
            campaign_id,
            {"intent": intent}
        )
    
    def emit_prompt_generation_completed(self, campaign_id: str, source: str):
        """Emit prompt generation completed event."""
        return self.emit(
            EventType.PROMPT_GENERATION_COMPLETED,
            campaign_id,
            {"source": source}  # 'cached' or 'generated'
        )
    
    def emit_call_started(
        self,
        campaign_id: str,
        call_uuid: str,
        to_number: str,
        from_number: str
    ):
        """Emit call started event."""
        return self.emit(
            EventType.CALL_STARTED,
            campaign_id,
            {
                "call_uuid": call_uuid,
                "to_number": to_number,
                "from_number": from_number
            }
        )
    
    def emit_call_completed(
        self,
        campaign_id: str,
        call_uuid: str,
        to_number: str,
        duration: int,
        hangup_cause: str
    ):
        """Emit call completed event."""
        return self.emit(
            EventType.CALL_COMPLETED,
            campaign_id,
            {
                "call_uuid": call_uuid,
                "to_number": to_number,
                "duration": duration,
                "hangup_cause": hangup_cause
            }
        )
    
    def emit_call_failed(
        self,
        campaign_id: str,
        call_uuid: str,
        to_number: str,
        error: str
    ):
        """Emit call failed event."""
        return self.emit(
            EventType.CALL_FAILED,
            campaign_id,
            {
                "call_uuid": call_uuid,
                "to_number": to_number,
                "error": error
            }
        )
    
    def emit_progress_update(self, campaign_id: str, progress: Dict[str, Any]):
        """Emit progress update event."""
        return self.emit(EventType.PROGRESS_UPDATE, campaign_id, progress)
    
    # ========================================================================
    # EVENT QUERIES
    # ========================================================================
    
    def get_events(
        self,
        campaign_id: str,
        event_types: List[str] = None,
        limit: int = 100,
        since: datetime = None
    ) -> List[Dict[str, Any]]:
        """
        Get events for a campaign.
        
        Args:
            campaign_id: Campaign identifier
            event_types: Filter by event types
            limit: Maximum results
            since: Only events after this timestamp
            
        Returns:
            List of events
        """
        query = {"campaign_id": campaign_id}
        
        if event_types:
            query["event_type"] = {"$in": event_types}
        
        if since:
            query["timestamp"] = {"$gte": since}
        
        events = list(
            events_collection.find(query, {"_id": 0})
            .sort("timestamp", -1)
            .limit(limit)
        )
        
        # Convert datetime to string
        for event in events:
            if isinstance(event.get("timestamp"), datetime):
                event["timestamp"] = event["timestamp"].isoformat()
        
        return events
    
    def get_recent_activity(self, org_id: str, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get recent activity across all campaigns for a user.
        
        Args:
            org_id: Organization ID
            user_id: User ID
            limit: Maximum results
            
        Returns:
            List of recent events
        """
        events = list(
            events_collection.find(
                {"org_id": org_id, "user_id": user_id},
                {"_id": 0}
            )
            .sort("timestamp", -1)
            .limit(limit)
        )
        
        for event in events:
            if isinstance(event.get("timestamp"), datetime):
                event["timestamp"] = event["timestamp"].isoformat()
        
        return events
    
    # ========================================================================
    # REAL-TIME SUBSCRIPTION (SSE)
    # ========================================================================
    
    def subscribe(self, campaign_id: str) -> Generator[Dict[str, Any], None, None]:
        """
        Subscribe to real-time events for a campaign.
        Yields events as they occur.
        
        Args:
            campaign_id: Campaign identifier
            
        Yields:
            Event dictionaries
        """
        channel = f"campaign:{campaign_id}:events"
        pubsub = redis_client.pubsub()
        pubsub.subscribe(channel)
        
        try:
            for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        event = json.loads(message["data"])
                        yield event
                    except json.JSONDecodeError:
                        continue
        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()
    
    async def subscribe_async(self, campaign_id: str) :
        """
        Async version of subscribe for use with async frameworks.
        
        Args:
            campaign_id: Campaign identifier
            
        Yields:
            Event dictionaries
        """
        channel = f"campaign:{campaign_id}:events"
        pubsub = redis_client.pubsub()
        pubsub.subscribe(channel)
        
        try:
            while True:
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message":
                    try:
                        event = json.loads(message["data"])
                        yield event
                    except json.JSONDecodeError:
                        continue
                await asyncio.sleep(0.1)  # Small delay to prevent busy loop
        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()


# Singleton instance
_event_service: Optional[TaskEventService] = None


def get_task_event_service() -> TaskEventService:
    """Get or create singleton instance."""
    global _event_service
    if _event_service is None:
        _event_service = TaskEventService()
    return _event_service
