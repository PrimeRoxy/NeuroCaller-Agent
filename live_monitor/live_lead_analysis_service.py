from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel
from services.call_service_voicelink import calls_collection
from live_monitor.live_call_socket import (
    schedule_live_call_update,
    resolve_call_context,
)

load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

LEAD_ANALYSIS_MODEL = os.getenv("LEAD_ANALYSIS_MODEL", "gemini-3.5-flash-lite")

ANALYSIS_DEBOUNCE_SECONDS = 1
MAX_CONTEXT_TURNS = int(os.getenv("LEAD_ANALYSIS_MAX_CONTEXT_TURNS", "10"))

client = genai.Client(api_key=GEMINI_API_KEY)


class LeadAnalysisResult(BaseModel):
    status: Literal["HOT", "WARM", "COLD"]
    reason: str


ANALYSIS_PROMPT = """
You are a real-time sales lead evaluator.

Evaluate ONLY the customer's current sales position.

Consider:
- customer intent
- level of interest
- responses to specific questions
- objections
- buying signals
- rejection
- pricing concerns
- willingness to continue
- likelihood of conversion

HOT:
Strong purchase intent or a clear buying signal.
Examples: wants to purchase, asks to proceed, requests a demo/meeting,
asks about next steps, or clearly wants to move forward.

WARM:
Interested or considering the offer, but not committed yet.
Examples: asks questions, needs more information, is comparing options,
or has an objection but remains open to the conversation.

COLD:
Clear rejection, no interest, repeated strong resistance, strong negative
intent, asks not to be contacted, or clearly indicates no purchase intent.

Rules:
1. Judge the customer, not the AI agent.
2. Use only evidence in the supplied conversation.
3. Do not invent facts.
4. If evidence is ambiguous, prefer WARM.
5. Return exactly one of HOT, WARM, or COLD.
6. Reason must be one short, specific, evidence-based sentence.
7. Return JSON only.
"""


async def initialize_live_call(
    call_uuid: str,
    from_number: str = "",
    to_number: str = "",
) -> None:
    """Create or update the live call document when the media socket starts."""

    if not call_uuid:
        return

    now = datetime.now(timezone.utc)

    # Tag the live doc with org/user/campaign so the dashboard can query and
    # scope it (read from Redis call_config; safe no-op if unavailable).
    org_id, user_id, campaign_id = resolve_call_context(call_uuid)

    set_fields = {
        "from_number": from_number,
        "to_number": to_number,
        "hangup_cause": "ongoing",
        "live_analysis_enabled": True,
        "updated_at": now,
    }
    if org_id:
        set_fields["organisation_id"] = org_id
    if user_id:
        set_fields["user_id"] = user_id
    if campaign_id:
        set_fields["campaign_id"] = campaign_id

    await asyncio.to_thread(
        calls_collection.update_one,
        {"call_uuid": call_uuid},
        {
            "$set": set_fields,
            "$setOnInsert": {
                "call_uuid": call_uuid,
                "lead_status": "WARM",
                "lead_reason": (
                    "Initial status; conversation not evaluated yet."
                ),
                "lead_status_history": [
                    {
                        "from_status": None,
                        "to_status": "WARM",
                        "status": "WARM",
                        "reason": (
                            "Initial status; conversation not evaluated yet."
                        ),
                        "timestamp": now,
                        "type": "initial",
                    }
                ],
            },
        },
        upsert=True,
    )

    # Push the initial "ongoing" state to the dashboard.
    schedule_live_call_update(call_uuid)


async def analyze_lead(
    call_uuid: str,
    conversation: str,
    state: dict,
) -> None:
    """Evaluate the conversation and update the same call document."""

    if not call_uuid or not conversation.strip():
        return

    prompt = f"""
{ANALYSIS_PROMPT}

Recent conversation:
{conversation}

Evaluate the customer's current lead status.
"""

    try:
        # Keep evaluations ordered so history cannot be written out of order.
        lock = state.setdefault(
            "lead_analysis_lock",
            asyncio.Lock(),
        )

        async with lock:

            response = await client.aio.models.generate_content(
                model=LEAD_ANALYSIS_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0,
                    max_output_tokens=120,
                    response_mime_type="application/json",
                    response_schema=LeadAnalysisResult,
                ),
            )

            result = LeadAnalysisResult.model_validate_json(
                response.text
            )

            status = result.status
            reason = result.reason.strip()
            now = datetime.now(timezone.utc)

            # Do not update after hangup.
            current = await asyncio.to_thread(
                calls_collection.find_one,
                {
                    "call_uuid": call_uuid,
                    "live_analysis_enabled": True,
                    "hangup_cause": "ongoing",
                },
                {
                    "lead_status": 1,
                    "lead_reason": 1,
                    "lead_status_history": 1,
                },
            )

            if not current:
                return

            previous_status = current.get("lead_status", "WARM")
            previous_reason = current.get("lead_reason", "")

            # Ignore completely identical consecutive evaluations.
            if (
                previous_status == status
                and previous_reason.strip().lower() == reason.lower()
            ):
                return

            history = current.get("lead_status_history", [])

            history_entry = {
                "from_status": previous_status,
                "to_status": status,
                "status": status,
                "reason": reason,
                "timestamp": now,
                "iteration": len(history) + 1,
                "type": "evaluation",
                "customer_turn": conversation,
            }

            result_update = await asyncio.to_thread(
                calls_collection.update_one,
                {
                    "call_uuid": call_uuid,
                    "live_analysis_enabled": True,
                    "hangup_cause": "ongoing",
                    "lead_status": previous_status,
                    "lead_reason": previous_reason,
                },
                {
                    "$set": {
                        "lead_status": status,
                        "lead_reason": reason,
                        "lead_status_updated_at": now,
                        "last_lead_evaluation_at": now,
                        "updated_at": now,
                    },
                    "$push": {
                        "lead_status_history": history_entry,
                    },
                },
            )

            if result_update.modified_count:
                logger.info(
                    "[LeadAnalysis] call=%s status=%s reason=%s",
                    call_uuid,
                    status,
                    reason,
                )
                # Push the updated lead status/reason to the dashboard.
                schedule_live_call_update(call_uuid)

    except asyncio.CancelledError:
        raise

    except Exception:
        logger.exception(
            "[LeadAnalysis] failed for call_uuid=%s",
            call_uuid,
        )


async def finalise_live_analysis(
    call_uuid: str,
) -> None:
    """Finalize lead history when the call ends."""

    if not call_uuid:
        return

    now = datetime.now(timezone.utc)

    current = await asyncio.to_thread(
        calls_collection.find_one,
        {"call_uuid": call_uuid},
        {
            "lead_status": 1,
            "lead_reason": 1,
            "lead_status_history": 1,
        },
    )

    if not current:
        return

    history = current.get("lead_status_history", [])

    statuses = []

    for item in history:
        status = item.get("to_status") or item.get("status")
        if status:
            statuses.append(status)

    transitions = []

    for index in range(1, len(statuses)):
        if statuses[index] != statuses[index - 1]:
            transitions.append(
                f"{statuses[index - 1]} → {statuses[index]}"
            )

    summary = {
        "initial_status": statuses[0] if statuses else "WARM",
        "final_status": current.get("lead_status", "WARM"),
        "final_reason": current.get("lead_reason", ""),
        "total_evaluations": len(history),
        "total_status_changes": len(transitions),
        "status_path": statuses,
        "transitions": transitions,
        "history": history,
        "finalized_at": now,
    }

    await asyncio.to_thread(
        calls_collection.update_one,
        {"call_uuid": call_uuid},
        {
            "$set": {
                "lead_status_summary": summary,
                "live_analysis_enabled": False,
                "lead_analysis_finalized_at": now,
                "updated_at": now,
            }
        },
    )

    # Push the final state (live_analysis_enabled=False) to the dashboard.
    schedule_live_call_update(call_uuid)


async def stop_live_analysis(call_uuid: str) -> None:
    """
    Existing dev function kept for compatibility.

    Finalizes history and disables further live-analysis writes.
    """

    await finalise_live_analysis(call_uuid)


async def schedule_lead_analysis_task(
    call_uuid: str,
    state: dict,
) -> None:
    """Wait for the customer turn to settle, then analyze it."""

    try:
        await asyncio.sleep(ANALYSIS_DEBOUNCE_SECONDS)

        customer_text = state.get("customer_buffer", "").strip()

        if not customer_text:
            return

        state["customer_buffer"] = ""

        state.setdefault("conversation_history", []).append(
            {
                "speaker": "customer",
                "text": customer_text,
            }
        )

        recent_turns = state["conversation_history"][
            -MAX_CONTEXT_TURNS:
        ]

        conversation = "\n".join(
            f"{item['speaker']}: {item['text']}"
            for item in recent_turns
        )

        task = asyncio.create_task(
            analyze_lead(
                call_uuid=call_uuid,
                conversation=conversation,
                state=state,
            )
        )

        state.setdefault("lead_analysis_tasks", set()).add(task)

        task.add_done_callback(
            lambda completed_task: state.get(
                "lead_analysis_tasks",
                set(),
            ).discard(completed_task)
        )

    except asyncio.CancelledError:
        raise

    except Exception:
        logger.exception(
            "[LeadAnalysis] scheduling failed for call_uuid=%s",
            call_uuid,
        )


def schedule_lead_analysis(
    call_uuid: str,
    state: dict,
) -> None:
    """
    Schedule analysis after the current customer turn settles.

    This function only creates an asyncio task, so the realtime
    websocket does not wait for the analysis.
    """

    existing_task = state.get("lead_analysis_debounce_task")

    if existing_task and not existing_task.done():
        existing_task.cancel()

    state["lead_analysis_debounce_task"] = asyncio.create_task(
        schedule_lead_analysis_task(
            call_uuid=call_uuid,
            state=state,
        )
    )