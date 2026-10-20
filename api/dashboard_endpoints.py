"""
Dashboard API Endpoints

Provides endpoints for agentic dashboards to monitor:
- Campaign status and progress
- Call metrics and outcomes
- Real-time event streaming (SSE)
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, Optional, List
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.campaign_tracking_service import (
    get_campaign_tracking_service,
    CampaignStatus
)
from services.task_event_service import get_task_event_service
from services.call_service_voicelink import calls_collection

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


# ============================================================================
# RESPONSE MODELS
# ============================================================================



class CampaignProgress(BaseModel):
    campaign_id: str
    status: str
    total: int
    completed: int
    failed: int
    pending: int
    in_progress: int


class StatsOverview(BaseModel):
    total_campaigns: int
    total_calls: int
    completed_calls: int
    failed_calls: int
    active_campaigns: int
    success_rate: float


# ============================================================================
# CAMPAIGN ENDPOINTS
# ============================================================================

@router.get("/campaigns")
async def list_campaigns(
    org_id: str = Query(..., description="Organization ID"),
    user_id: str = Query(..., description="User ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(20, ge=1, le=100, description="Maximum results"),
    skip: int = Query(0, ge=0, description="Results to skip")
):
    """
    List all campaigns for an organization/user.
    
    Returns campaigns sorted by creation date (newest first).
    """
    tracking_service = get_campaign_tracking_service()
    
    campaigns = tracking_service.list_campaigns(
        org_id=org_id,
        user_id=user_id,
        status=status,
        limit=limit,
        skip=skip
    )
    
    return campaigns


@router.get("/campaign/{campaign_id}")
async def get_campaign_details(campaign_id: str):
    """
    Get detailed campaign information including real-time progress.
    
    Returns:
        Campaign details with current status and call counts
    """
    tracking_service = get_campaign_tracking_service()
    
    campaign = tracking_service.get_campaign(campaign_id)
    
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    return campaign


@router.get("/campaign/{campaign_id}/progress", response_model=CampaignProgress)
async def get_campaign_progress(campaign_id: str):
    """
    Get real-time campaign progress from Redis.
    
    This is a lightweight endpoint for frequent polling.
    """
    tracking_service = get_campaign_tracking_service()

    progress = tracking_service.get_progress(campaign_id)

    if progress.get("status") == "unknown":
        raise HTTPException(status_code=404, detail="Campaign not found or expired")
    
    return progress


@router.get("/campaign/{campaign_id}/calls")
async def get_campaign_calls(
    campaign_id: str,
):
    """
    Get all calls in a campaign with their status and details.
    
    Returns:
        List of call records with recording URLs and transcripts
    """
    calls = list(
        calls_collection.find(
            {"campaign_id": campaign_id},
            {"_id": 0}
        )
        .sort("start_time", -1)
    )
    
    # Convert datetime fields
    for call in calls:
        for key in ["start_time", "end_time", "transcription_received_at"]:
            if call.get(key) and isinstance(call[key], datetime):
                call[key] = call[key].isoformat()
    
    total = calls_collection.count_documents({"campaign_id": campaign_id})
    
    return {
        "campaign_id": campaign_id,
        "calls": calls,
        "count": len(calls),
        "total": total
    }


@router.get("/campaign/{campaign_id}/events")
def get_campaign_events(
    campaign_id: str,
    event_types: Optional[str] = Query(None, description="Comma-separated event types to filter")
):
    """
    Get historical events for a campaign.
    
    Use this for reviewing past activity, not real-time monitoring.
    For real-time updates, use the SSE endpoint.
    """
    event_service = get_task_event_service()
    
    types_list = event_types.split(",") if event_types else None
    
    events = event_service.get_events(
        campaign_id=campaign_id,
        event_types=types_list
    )
    
    return {
        "campaign_id": campaign_id,
        "events": events,
        "count": len(events)
    }


# ============================================================================
# REAL-TIME STREAMING (SSE)
# ============================================================================

@router.get("/campaign/{campaign_id}/stream")
def stream_campaign_events(campaign_id: str):
    """
    Server-Sent Events (SSE) stream for real-time campaign updates.
    
    Connect to this endpoint to receive live updates about:
    - Call starts/completions
    - Progress changes
    - Campaign status changes
    
    Example usage:
    ```javascript
    const eventSource = new EventSource('/api/dashboard/campaign/{id}/stream');
    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log('Event:', data);
    };
    ```
    """
    tracking_service = get_campaign_tracking_service()
    
    # Verify campaign exists
    campaign =tracking_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    async def event_generator():
        event_service = get_task_event_service()
        channel = f"campaign:{campaign_id}:events"
        pubsub = event_service._pubsub or None
        
        # Create pubsub connection
        from core.config import redis_client
        pubsub = redis_client.pubsub()
        pubsub.subscribe(channel)
        
        try:
            # Send initial connection event
            initial_progress = await tracking_service.get_progress(campaign_id)
            yield f"event: connected\ndata: {json.dumps(initial_progress)}\n\n"
            
            # Stream events
            while True:
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message":
                    event_data = message["data"]
                    yield f"data: {event_data}\n\n"
                
                # Send heartbeat every 30 seconds
                await asyncio.sleep(0.1)
                
                # Check if campaign is still active
                status = redis_client.get(f"campaign:{campaign_id}:status")
                if status in [CampaignStatus.COMPLETED, CampaignStatus.FAILED, CampaignStatus.CANCELLED]:
                    yield f"event: campaign_ended\ndata: {json.dumps({'status': status})}\n\n"
                    break
                    
        except asyncio.CancelledError:
            pass
        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ============================================================================
# STATISTICS ENDPOINTS
# ============================================================================


class MemberStat(BaseModel):
    user_id: str
    total_calls: int
    outcomes: Dict[str, int]
    hangup_causes: Dict[str, int]

class DashboardSummary(BaseModel):
    # Campaign Stats
    total_campaigns: int
    active_campaigns: int
    total_calls: int
    completed_calls: int
    failed_calls: int
    campaign_success_rate: float
    
    # Call Metrics
    total_duration_minutes: float
    avg_duration_minutes: float
    call_success_rate: float
    hangup_causes: Dict[str, int]
    outcome_counts: Dict[str, int]
    follow_up_required_count: int
    
    # Member Data
    member_stats: List[MemberStat]


@router.get("/stats/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    org_id: str = Query(..., description="Organization ID"),
    user_id: Optional[str] = Query(None, description="User ID")
):
    """
    Get a complete, optimized summary of dashboard statistics.
    Merges campaign overview and detailed call metrics into a single high-performance query.
    If user_id is provided, filters by user. Otherwise, returns organization-wide stats.
    Includes member-wise breakdown of call data.
    """
    tracking_service = get_campaign_tracking_service()
    
    # Build match query dynamically
    calls_match = {"organisation_id": org_id}
    if user_id:
        calls_match["user_id"] = user_id
    
    # Define aggregation pipeline for calls (Optimized with $facet)
    calls_pipeline = [
        {"$match": calls_match},
        {"$facet": {
            "general": [
                {"$group": {
                    "_id": None,
                    "count": {"$sum": 1},
                    "total_duration": {"$sum": "$duration"},
                    "avg_duration": {"$avg": "$duration"}
                }}
            ],
            "hangup_causes": [
                {"$group": {"_id": "$hangup_cause", "count": {"$sum": 1}}}
            ],
            "outcomes": [
                {"$group": {"_id": "$outcome", "count": {"$sum": 1}}}
            ],
            "follow_ups": [
                {"$match": {"follow_up_required": True}},
                {"$count": "count"}
            ],
            "successful_calls": [
                {"$match": {"hangup_cause": {"$in": ["Normal Hangup", "NORMAL_CLEARING"]}}},
                {"$count": "count"}
            ],
            # Member-wise breakdowns (scalable grouping)
            "member_outcomes": [
                {"$group": {
                    "_id": {"user_id": "$user_id", "outcome": "$outcome"},
                    "count": {"$sum": 1}
                }}
            ],
            "member_hangups": [
                {"$group": {
                    "_id": {"user_id": "$user_id", "hangup_cause": "$hangup_cause"},
                    "count": {"$sum": 1}
                }}
            ]
        }}
    ]

    # Function to run call aggregation in thread
    def fetch_call_metrics():
        result = list(calls_collection.aggregate(calls_pipeline))
        return result[0] if result else {}

    # Run both queries in parallel (Campaign Stats + Call Metrics)
    # tracking_service.get_stats_overview is already defined, we run it in a thread alongside calls
    campaign_stats_task = asyncio.to_thread(tracking_service.get_stats_overview, org_id, user_id)
    call_metrics_task = asyncio.to_thread(fetch_call_metrics)
    
    campaign_stats, call_raw = await asyncio.gather(campaign_stats_task, call_metrics_task)
    
    # --- Process Call Metrics ---
    general = call_raw.get("general", [])
    general_stats = general[0] if general else {"count": 0, "total_duration": 0, "avg_duration": 0}
    
    # Hangup Causes map
    hangup_causes_list = call_raw.get("hangup_causes", [])
    hangup_causes = {item["_id"]: item["count"] for item in hangup_causes_list if item["_id"]}
    
    # Outcomes map
    outcomes_list = call_raw.get("outcomes", [])
    outcome_counts_map = {item["_id"]: item["count"] for item in outcomes_list if item["_id"]}
    
    # Ensure all keys exist for consistency
    outcome_counts = {
        "Qualified": outcome_counts_map.get("Qualified", 0),
        "Follow-up required": outcome_counts_map.get("Follow-up required", 0),
        "Not interested": outcome_counts_map.get("Not interested", 0),
        "Unreachable": outcome_counts_map.get("Unreachable", 0),
        "Unclassified": outcome_counts_map.get("Unclassified", 0) # Handle pre-existing or nulls
    }
    # Add any others that might exist dynamically
    for k, v in outcome_counts_map.items():
        if k not in outcome_counts:
            outcome_counts[k] = v
            
    # Follow ups
    follow_ups_list = call_raw.get("follow_ups", [])
    follow_up_count = follow_ups_list[0]["count"] if follow_ups_list else 0
    
    # Success Rate
    success_list = call_raw.get("successful_calls", [])
    success_count = success_list[0]["count"] if success_list else 0
    total_calls_count = general_stats.get("count", 0)
    call_success_rate = round((success_count / total_calls_count * 100), 2) if total_calls_count > 0 else 0.0

    # --- Process Member Stats ---
    member_data = {}

    # Process member outcomes
    for item in call_raw.get("member_outcomes", []):
        uid = item["_id"].get("user_id") or "Unknown"
        outcome = item["_id"].get("outcome") or "Unclassified"
        count = item["count"]
        
        if uid not in member_data:
            member_data[uid] = {"outcomes": {}, "hangups": {}, "total": 0}
            
        member_data[uid]["outcomes"][outcome] = count
        member_data[uid]["total"] += count

    # Process member hangups
    for item in call_raw.get("member_hangups", []):
        uid = item["_id"].get("user_id") or "Unknown"
        cause = item["_id"].get("hangup_cause") or "Unknown"
        count = item["count"]
        
        # Note: We rely on outcomes loop to init user, but let's be safe
        if uid not in member_data:
            member_data[uid] = {"outcomes": {}, "hangups": {}, "total": 0}
            # If a user has NO outcomes (unlikely if they have hangups), we count total here carefully
            # Actually total is best counted from just one source if strictly 1:1, but outcomes might be missing.
            # However, MongoDB total_calls facet is accurate.
            # Here we just accumulate breakdown data.
            # Let's trust total from outcomes loop mostly, but if we map properly...
            # Actually, total calls is best computed by summing all hangups (since every call has a hangup/status).
            # Let's re-calc total from hangups for accuracy.
        
        member_data[uid]["hangups"][cause] = count
    
    # Construct MemberStat objects
    member_stats = []
    for uid, data in member_data.items():
        # Re-verify total from hangups if preferred, or just use what we have
        # A simple sum of hangups is usually the total calls
        total_calls = sum(data["hangups"].values())
        if total_calls == 0: total_calls = data["total"] # Fallback
        
        member_stats.append(MemberStat(
            user_id=uid,
            total_calls=total_calls,
            outcomes=data["outcomes"],
            hangup_causes=data["hangups"]
        ))
    
    # Sort by total calls descending
    member_stats.sort(key=lambda x: x.total_calls, reverse=True)

    return DashboardSummary(
        # Campaign Data
        total_campaigns=campaign_stats.get("total_campaigns", 0),
        active_campaigns=campaign_stats.get("active_campaigns", 0),
        total_calls=campaign_stats.get("total_calls", 0), # From campaigns perspective
        completed_calls=campaign_stats.get("completed_calls", 0),
        failed_calls=campaign_stats.get("failed_calls", 0),
        campaign_success_rate=campaign_stats.get("success_rate", 0),
        
        # Call Data
        total_duration_minutes=round(general_stats.get("total_duration", 0) / 60, 2),
        avg_duration_minutes=round(general_stats.get("avg_duration", 0) / 60, 2),
        call_success_rate=call_success_rate,
        hangup_causes=hangup_causes,
        outcome_counts=outcome_counts,
        follow_up_required_count=follow_up_count,
        
        # Member Data
        member_stats=member_stats
    )


# ============================================================================
# CAMPAIGN ACTIONS
# ============================================================================

@router.post("/campaign/{campaign_id}/pause")
async def pause_campaign(campaign_id: str):
    """
    Pause a running campaign.
    
    Note: This stops new calls from being initiated but doesn't
    terminate calls already in progress.
    """
    tracking_service = get_campaign_tracking_service()
    
    campaign = tracking_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    if campaign.get("status") != CampaignStatus.RUNNING:
        raise HTTPException(status_code=400, detail="Campaign is not running")
    
    success = tracking_service.update_status(campaign_id, CampaignStatus.PAUSED)
    
    if success:
        event_service = get_task_event_service()
        event_service.emit(
            "CAMPAIGN_PAUSED",
            campaign_id,
            {},
            campaign.get("org_id"),
            campaign.get("user_id")
        )
    
    return {"status": "paused", "campaign_id": campaign_id}


@router.post("/campaign/{campaign_id}/resume")
async def resume_campaign(campaign_id: str):
    """
    Resume a paused campaign.
    """
    tracking_service = get_campaign_tracking_service()
    
    campaign = tracking_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    if campaign.get("status") != CampaignStatus.PAUSED:
        raise HTTPException(status_code=400, detail="Campaign is not paused")
    
    success = tracking_service.update_status(campaign_id, CampaignStatus.RUNNING)
    
    if success:
        event_service = get_task_event_service()
        event_service.emit(
            "CAMPAIGN_RESUMED",
            campaign_id,
            {},
            campaign.get("org_id"),
            campaign.get("user_id")
        )
    
    return {"status": "running", "campaign_id": campaign_id}


@router.post("/campaign/{campaign_id}/cancel")
async def cancel_campaign(campaign_id: str):
    """
    Cancel a campaign.
    
    This stops all pending calls and marks the campaign as cancelled.
    """
    tracking_service = get_campaign_tracking_service()
    
    campaign = tracking_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    if campaign.get("status") in [CampaignStatus.COMPLETED, CampaignStatus.CANCELLED]:
        raise HTTPException(status_code=400, detail="Campaign is already finished")
    
    # Clear Redis queue
    from core.config import redis_client
    queue_key = f"call_queue:{campaign_id}"
    redis_client.delete(queue_key)
    
    # Update status
    success = tracking_service.update_status(campaign_id, CampaignStatus.CANCELLED)
    
    if success:
        event_service = get_task_event_service()
        event_service.emit(
            "CAMPAIGN_CANCELLED",
            campaign_id,
            {},
            campaign.get("org_id"),
            campaign.get("user_id")
        )
    
    return {"status": "cancelled", "campaign_id": campaign_id}
