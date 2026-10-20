"""
Quiet Hours Watcher

Starting a campaign is already gated on quiet hours (agent/tools.py), but a
campaign that is *already running* when the window opens keeps dialing — one
lead per hangup — all night. This watcher closes that gap: it wakes exactly on
each org's quiet-hours boundary, finds campaigns still marked 'running', and
pauses them.

Enforcement is free after that. Every provider's process_campaign_queue() bails
on `status != "running"` (call_service.py:255, call_service_plivo.py:300,
call_service_voicelink.py:152), so flipping the flag is the whole mechanism. A
call already in flight is never cut off: it finishes normally, and its hangup
webhook is what discovers the paused status and stops the chain. Leads still in
the Redis queue are left in place, in order, for a later resume.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import pytz

from core.config import redis_client
from services.call_service_voicelink import orgcalls_collection
from services.campaign_tracking_service import (
    CampaignStatus,
    campaign_keys,
    campaigns_collection,
    get_campaign_tracking_service,
)
from services.config_service import ConfigService, parse_time_string, resolve_timezone
from services.task_event_service import EventType, get_task_event_service

logger = logging.getLogger("QuietHours")

# Marks a pause this watcher performed so it can be told apart from one a human
# asked for — a later auto-resume must never un-pause a campaign someone
# deliberately stopped.
PAUSE_REASON = "quiet_hours"

# Longest we ever sleep between sweeps. The boundary itself is still hit exactly
# (the sleep is recomputed every pass), but capping it means quiet hours edited
# at 20:00 are picked up instead of being ignored by a loop parked on the old
# time — and a sweep still runs periodically when no boundary is configured.
MAX_SLEEP_SECONDS = 900

# India has no DST, so default to Asia/Kolkata
DEFAULT_TIMEZONE = "Asia/Kolkata"


def _resolve_timezone(timezone_str: Optional[str]):
    """Org timezone, falling back to Asia/Kolkata."""
    return resolve_timezone(timezone_str)


def seconds_until_next_boundary() -> Optional[float]:
    """
    Seconds until the soonest quiet-hours start across every configured org,
    or None when no org has one set.
    """
    configs = orgcalls_collection.find(
        {"quiet_hours_start": {"$exists": True, "$ne": None}},
        {"_id": 0, "organisation_id": 1, "quiet_hours_start": 1, "timezone": 1},
    )

    soonest = None
    for config in configs:
        start_str = config.get("quiet_hours_start")
        if not start_str:
            continue

        try:
            start_time = parse_time_string(start_str)
        except ValueError as parse_err:
            # One org's bad config must not stop us waking for everyone else.
            logger.error(
                f"[QuietHours] Org {config.get('organisation_id')} has unparseable "
                f"quiet_hours_start '{start_str}': {parse_err}"
            )
            continue

        tz = _resolve_timezone(config.get("timezone"))
        now_local = datetime.now(tz)

        boundary = tz.localize(datetime.combine(now_local.date(), start_time))
        if boundary <= now_local:
            # Today's boundary has already passed; the next one is tomorrow.
            boundary = tz.localize(
                datetime.combine(now_local.date() + timedelta(days=1), start_time)
            )

        delta = (boundary - now_local).total_seconds()
        if soonest is None or delta < soonest:
            soonest = delta

    return soonest


def _pause_campaign(
    campaign_id: str,
    org_id: str,
    user_id: Optional[str],
    reason: str
) -> None:
    """Flip the campaign to paused and record that quiet hours is why."""
    tracking = get_campaign_tracking_service()
    tracking.update_status(campaign_id, CampaignStatus.PAUSED)

    # update_status writes only the status itself, so stamp the reason
    # separately for the dashboard and for any future auto-resume.
    campaigns_collection.update_one(
        {"campaign_id": campaign_id},
        {"$set": {"paused_reason": PAUSE_REASON, "paused_at": datetime.utcnow()}},
    )

    try:
        events = get_task_event_service()
        events.emit(
            EventType.CAMPAIGN_PAUSED,
            campaign_id,
            {"reason": PAUSE_REASON, "message": reason},
            org_id,
            user_id,
        )
    except Exception as event_err:
        logger.warning(
            f"[QuietHours] Failed to emit CAMPAIGN_PAUSED for {campaign_id}: {event_err}"
        )


def sweep_running_campaigns() -> Dict[str, Any]:
    """
    Pause every running campaign whose org is currently inside its quiet hours.

    Safe to call at any time and as often as you like — campaigns outside a
    quiet window are left alone, so a no-op sweep costs one indexed query.

    Returns:
        {"scanned": int, "paused": [campaign_id, ...]}
    """
    candidates = list(
        campaigns_collection.find(
            {"status": CampaignStatus.RUNNING},
            {"_id": 0, "campaign_id": 1, "org_id": 1, "user_id": 1},
        )
    )

    if not candidates:
        return {"scanned": 0, "paused": []}

    paused = []
    # One quiet-hours lookup per org rather than per campaign.
    org_verdicts: Dict[str, Tuple[bool, str]] = {}

    for campaign in candidates:
        campaign_id = campaign.get("campaign_id")
        org_id = campaign.get("org_id")
        if not campaign_id or not org_id:
            continue

        # Mongo is the synced copy; Redis holds the live status.
        live_status = redis_client.get(campaign_keys(campaign_id)["status"])
        if live_status and live_status != CampaignStatus.RUNNING:
            continue

        if org_id not in org_verdicts:
            org_verdicts[org_id] = ConfigService.is_quiet_hours(org_id)
        is_quiet, reason = org_verdicts[org_id]

        if not is_quiet:
            continue

        try:
            _pause_campaign(campaign_id, org_id, campaign.get("user_id"), reason)
            paused.append(campaign_id)
            logger.info(
                f"[QuietHours] Paused running campaign {campaign_id} (org {org_id}) "
                f"— now inside quiet hours"
            )
        except Exception as pause_err:
            logger.error(f"[QuietHours] Failed to pause {campaign_id}: {pause_err}")

    return {"scanned": len(candidates), "paused": paused}


async def run_quiet_hours_watcher():
    """
    Background loop: sweep now, then sleep until the next quiet-hours boundary.

    The opening sweep matters as much as the timer. If the server was down or
    mid-deploy when the window opened, a pure alarm has already missed its shot
    and the campaign would dial until morning; sweeping on boot makes the
    watcher self-healing across restarts.
    """
    logger.info("[QuietHours] Starting quiet hours watcher...")

    loop = asyncio.get_running_loop()

    while True:
        try:
            # Mongo and Redis calls here are blocking; keep them off the event loop.
            result = await loop.run_in_executor(None, sweep_running_campaigns)
            if result["scanned"]:
                logger.info(
                    f"[QuietHours] Swept {result['scanned']} running campaign(s), "
                    f"paused {len(result['paused'])}."
                )

            next_boundary = await loop.run_in_executor(None, seconds_until_next_boundary)

            if next_boundary is None:
                sleep_seconds = MAX_SLEEP_SECONDS
                logger.info("[QuietHours] No org has quiet hours configured; re-checking in 15m.")
            else:
                sleep_seconds = min(next_boundary, MAX_SLEEP_SECONDS)
                logger.info(
                    f"[QuietHours] Next boundary in {next_boundary:.0f}s; "
                    f"sleeping {sleep_seconds:.0f}s."
                )

            # Floor of 1s: waking a hair early must not spin the loop.
            await asyncio.sleep(max(sleep_seconds, 1))

        except Exception as e:
            logger.error(f"[QuietHours] Watcher loop error: {e}")
            await asyncio.sleep(60)
