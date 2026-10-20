"""
Live Call Socket — test client
==============================

Two modes:

1) Listen (default): connect to the dashboard WebSocket and print every frame.

    python -m live_monitor.live_call_client \
        --url ws://localhost:8000 --org ORG123 --user USER456

2) Simulate: publish fake call updates straight to the Redis channel the
   socket reads, so you can verify the pipe without placing a real call.
   Run this in a second terminal while the listener (and server) are up.

    python -m live_monitor.live_call_client --simulate \
        --org ORG123 --user USER456

You can also open live_call_test.html in a browser for a visual test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from datetime import datetime, timezone


def _listen(base_url: str, org_id: str, user_id: str) -> None:
    import websockets  # dependency already used by the call services

    url = f"{base_url.rstrip('/')}/ws/live-calls?org_id={org_id}&user_id={user_id}"

    async def run() -> None:
        print(f"[client] connecting {url}")
        async with websockets.connect(url) as ws:
            print("[client] connected — waiting for frames (Ctrl-C to quit)\n")
            async for raw in ws:
                frame = json.loads(raw)
                kind = frame.get("type")
                if kind == "snapshot":
                    print(f"SNAPSHOT ({len(frame['calls'])} call(s)):")
                    for c in frame["calls"]:
                        print("   ", _fmt(c))
                elif kind == "update":
                    print("UPDATE   ", _fmt(frame["call"]))
                elif kind == "ping":
                    print("· ping")
                else:
                    print("FRAME    ", frame)

    asyncio.run(run())


def _fmt(call: dict) -> str:
    return (
        f"{call.get('call_uuid', '')} "
        f"{call.get('from_number')}→{call.get('to_number')} "
        f"status={call.get('lead_status')} "
        f"cause={call.get('hangup_cause')} "
        f"live={call.get('live_analysis_enabled')} "
        f"reason={(call.get('lead_reason') or '')[:50]!r}"
    )


def _simulate(org_id: str, user_id: str) -> None:
    from core.config import redis_client
    from live_monitor.live_call_socket import channel_name

    channel = channel_name(org_id, user_id)
    call_uuid = str(uuid.uuid4())

    steps = [
        ("ongoing", "WARM", True, "Initial status; conversation not evaluated yet."),
        ("ongoing", "WARM", True, "Customer asked about pricing — WARM."),
        ("ongoing", "HOT", True, "Customer requested a demo — HOT."),
        ("Normal Hangup", "COLD", False, "Customer explicitly declined an immediate call."),
    ]

    print(f"[simulate] channel={channel} call_uuid={call_uuid}")
    for cause, status, live, reason in steps:
        payload = {
            "call_uuid": call_uuid,
            "from_number": "917969460176",
            "to_number": "919113797240",
            "hangup_cause": cause,
            "lead_status": status,
            "lead_reason": reason,
            "live_analysis_enabled": live,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        n = redis_client.publish(channel, json.dumps(payload))
        print(f"[simulate] published cause={cause!r} → {n} subscriber(s)")
        time.sleep(1.5)

    print("[simulate] done")


def main() -> None:
    ap = argparse.ArgumentParser(description="Live call socket test client")
    ap.add_argument("--url", default="ws://localhost:8000", help="WS server base URL")
    ap.add_argument("--org", required=True, help="org_id")
    ap.add_argument("--user", required=True, help="user_id")
    ap.add_argument(
        "--simulate",
        action="store_true",
        help="publish fake updates to Redis instead of listening",
    )
    args = ap.parse_args()

    if args.simulate:
        _simulate(args.org, args.user)
    else:
        _listen(args.url, args.org, args.user)


if __name__ == "__main__":
    main()
