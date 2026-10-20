"""
Live Call Socket
================

Self-contained, additive layer that streams live call/lead updates to the
frontend dashboard over a WebSocket, keyed by (org_id, user_id).

Nothing here changes the existing call or lead-analysis logic. It only:

  1. `publish_live_call_update(call_uuid)` — after a Mongo write, publish the
     7 dashboard fields to a per-user Redis channel.
  2. `router` — a WebSocket endpoint the dashboard connects to with
     ?org_id=..&user_id=..  It sends a snapshot on connect, then forwards
     every published update for that user in real time.

Routing key resolution
-----------------------
The frontend subscribes with org_id + user_id, but the live call document is
keyed only by call_uuid. During a live call the org/user live in Redis at
`call_config:{call_uuid}` (set by the call service); after the call they are
also on the `calls` document. We try both, Redis first.

Payload (exactly the fields the dashboard maps by call_uuid)
------------------------------------------------------------
    call_uuid, from_number, to_number, hangup_cause,
    lead_reason, live_analysis_enabled, updated_at
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from core.config import redis_client
from services.call_service_voicelink import calls_collection

logger = logging.getLogger(__name__)

router = APIRouter()

# Fields we read from Mongo: the 7 dashboard fields + routing/scoping fields.
_PROJECTION = {
    "_id": 0,
    "call_uuid": 1,
    "from_number": 1,
    "to_number": 1,
    "hangup_cause": 1,
    "lead_status": 1,
    "lead_reason": 1,
    "live_analysis_enabled": 1,
    "updated_at": 1,
    # routing + campaign scoping (not sent to the client)
    "organisation_id": 1,
    "user_id": 1,
    "campaign_id": 1,
}


# ---------------------------------------------------------------------------
# Channel + serialization helpers
# ---------------------------------------------------------------------------

def channel_name(org_id: str, user_id: str) -> str:
    """Per-user pub/sub channel the dashboard subscribes to."""
    return f"live_calls:{org_id}:{user_id}"


def _iso(value) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _to_payload(doc: dict) -> dict:
    """Project a Mongo call document down to the 7 dashboard fields."""
    return {
        "call_uuid": doc.get("call_uuid"),
        "from_number": doc.get("from_number"),
        "to_number": doc.get("to_number"),
        "hangup_cause": doc.get("hangup_cause"),
        "lead_status": doc.get("lead_status"),
        "lead_reason": doc.get("lead_reason"),
        "live_analysis_enabled": doc.get("live_analysis_enabled"),
        "updated_at": _iso(doc.get("updated_at")),
    }


def resolve_call_context(
    call_uuid: str, doc: dict | None = None
) -> tuple[str | None, str | None, str | None]:
    """
    Find (org_id, user_id, campaign_id) for a call.

    Redis `call_config:{call_uuid}` is the source of truth during a live call
    (the call doc isn't tagged with org/user until initialize_live_call runs);
    the doc is the fallback afterwards.
    """
    try:
        raw = redis_client.get(f"call_config:{call_uuid}")
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            cfg = json.loads(raw)
            org_id = cfg.get("organisation_id")
            user_id = cfg.get("user_id")
            campaign_id = cfg.get("campaign_id")
            if org_id and user_id:
                return org_id, user_id, campaign_id
    except Exception:
        logger.exception("[LiveSocket] call_config lookup failed for %s", call_uuid)

    if doc:
        return (
            doc.get("organisation_id"),
            doc.get("user_id"),
            doc.get("campaign_id"),
        )
    return None, None, None


# ---------------------------------------------------------------------------
# Publish (called right after each Mongo write)
# ---------------------------------------------------------------------------

async def publish_live_call_update(call_uuid: str, doc: dict | None = None) -> None:
    """
    Publish the current state of a call to its per-user channel.

    Safe to call fire-and-forget; never raises into the caller.

    Args:
        call_uuid: the call to publish.
        doc: optional in-hand document; if omitted it is read from Mongo so
             the payload always matches what was just written.
    """
    if not call_uuid:
        return

    try:
        if doc is None:
            doc = await asyncio.to_thread(
                calls_collection.find_one,
                {"call_uuid": call_uuid},
                _PROJECTION,
            )
        if not doc:
            return

        org_id, user_id, _campaign_id = resolve_call_context(call_uuid, doc)
        if not org_id or not user_id:
            logger.warning(
                "[LiveSocket] no org/user for call=%s; skip publish", call_uuid
            )
            return

        payload = _to_payload(doc)
        await asyncio.to_thread(
            redis_client.publish,
            channel_name(org_id, user_id),
            json.dumps(payload),
        )
        logger.debug(
            "[LiveSocket] published call=%s org=%s user=%s cause=%s",
            call_uuid, org_id, user_id, payload.get("hangup_cause"),
        )
    except Exception:
        logger.exception("[LiveSocket] publish failed for call=%s", call_uuid)


def schedule_live_call_update(call_uuid: str) -> None:
    """Fire-and-forget helper — one line for the Mongo write sites to call."""
    try:
        asyncio.create_task(publish_live_call_update(call_uuid))
    except RuntimeError:
        # No running loop (e.g. sync context) — publish inline in a thread.
        asyncio.run(publish_live_call_update(call_uuid))


# ---------------------------------------------------------------------------
# Snapshot on connect
# ---------------------------------------------------------------------------

def _fetch_snapshot(org_id: str, user_id: str, limit: int = 2) -> list[dict]:
    """
    Hydrate a dashboard that opens mid-campaign with the current campaign's
    most recent calls (newest first), at most `limit` of them.

    Scoped to the CURRENT campaign so old completed calls from previous
    campaigns don't leak in. The current campaign is the one the newest call
    for this org+user belongs to. This gives the expected progression:
      - only the ongoing call at campaign start  -> 1 card
      - the completed call + the next ongoing one -> 2 cards
      - always the two most recent thereafter     -> 2 cards
    """
    base = {"organisation_id": org_id, "user_id": user_id}

    latest = calls_collection.find_one(
        base, _PROJECTION, sort=[("updated_at", -1)]
    )
    if not latest:
        return []

    query = dict(base)
    campaign_id = latest.get("campaign_id")
    if campaign_id:
        query["campaign_id"] = campaign_id

    cursor = (
        calls_collection.find(query, _PROJECTION)
        .sort("updated_at", -1)
        .limit(limit)
    )
    return [_to_payload(doc) for doc in cursor]


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws/live-calls")
async def live_calls_ws(
    websocket: WebSocket,
    org_id: str = Query(...),
    user_id: str = Query(...),
):
    """
    Dashboard connects here with ?org_id=..&user_id=..

    Frames sent to the client:
      {"type": "snapshot", "calls": [ ...up to 2 payloads... ]}
      {"type": "update",   "call": { ...7 fields... }}
      {"type": "ping"}   (heartbeat, ~every 20s)
    """
    await websocket.accept()

    channel = channel_name(org_id, user_id)
    pubsub = redis_client.pubsub()
    pubsub.subscribe(channel)

    forwarder: asyncio.Task | None = None

    async def forward() -> None:
        """Pump Redis pub/sub -> WebSocket, with a periodic heartbeat."""
        last_ping = asyncio.get_event_loop().time()
        while True:
            message = await asyncio.to_thread(
                pubsub.get_message,
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if message and message.get("type") == "message":
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                await websocket.send_text(
                    json.dumps({"type": "update", "call": json.loads(data)})
                )

            now = asyncio.get_event_loop().time()
            if now - last_ping >= 20:
                await websocket.send_text(json.dumps({"type": "ping"}))
                last_ping = now

    try:
        # 1) snapshot
        snapshot = await asyncio.to_thread(_fetch_snapshot, org_id, user_id)
        await websocket.send_text(
            json.dumps({"type": "snapshot", "calls": snapshot})
        )

        # 2) live updates, while also watching for client disconnect
        forwarder = asyncio.create_task(forward())
        try:
            while True:
                # receive_text() resolves (raises WebSocketDisconnect) when the
                # client closes; we don't expect real inbound messages.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(
            "[LiveSocket] ws error org=%s user=%s", org_id, user_id
        )
    finally:
        if forwarder:
            forwarder.cancel()
            try:
                await forwarder
            except asyncio.CancelledError:
                pass
        await asyncio.to_thread(pubsub.unsubscribe, channel)
        await asyncio.to_thread(pubsub.close)
        logger.info("[LiveSocket] closed org=%s user=%s", org_id, user_id)
