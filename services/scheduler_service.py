import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from core.config import redis_client

logger = logging.getLogger("Scheduler")
SCHEDULE_KEY = "scheduled_callbacks"
SCHEDULER_CHANNEL = "scheduler_channel"

# India Standard Time (UTC+05:30). Callback times are reasoned about in IST, so a
# naive run_at (no tzinfo) is treated as an IST wall-clock time rather than the
# server's local timezone — otherwise the epoch score is off and the call fires early.
IST = timezone(timedelta(hours=5, minutes=30))

def resolve_provider(phone_number: Optional[str] = None, provider: Optional[str] = None) -> str:
    """
    Resolves provider ('frejun', 'voicelink', 'twilio', 'plivo') based on explicit provider tag or phone number.
    """
    if provider and str(provider).lower() in ("frejun", "voicelink", "twilio", "plivo"):
        return str(provider).lower()
        
    if not phone_number:
        return "frejun"
        
    clean_num = str(phone_number).strip()
    if clean_num in {"+18382064847"}:
        return "twilio"
    elif clean_num in {"+97148987384"} or clean_num.startswith("+971") or clean_num.startswith("971"):
        return "frejun"
    elif clean_num in {"+917969460176"} or clean_num.startswith("+91") or "917969460176" in clean_num:
        return "voicelink"
    else:
        return "plivo"

def initiate_provider_call(
    provider: str,
    to_number: str,
    agent_number: str,
    welcome_message: Optional[str] = None,
    system_prompt: Optional[str] = None,
    campaign_id: Optional[str] = None,
    lead_name: str = "",
    voice: str = "ash",
    organisation_id: Optional[str] = None,
    user_id: Optional[str] = None
) -> bool:
    """
    Dispatches outbound call initiation to the specific provider service.
    """
    resolved_prov = resolve_provider(agent_number, provider)
    logger.info(f"[Scheduler-Dispatch] Initiating callback call via {resolved_prov.upper()} (agent_number: {agent_number}, to: {to_number})")
    
    try:
        # if resolved_prov == "frejun":
        #     from services.call_service_frejun import call_initiate
        #     return call_initiate(
        #         to_number=to_number,
        #         agent_phone_number=agent_number,
        #         welcome_message=welcome_message,
        #         system_prompt=system_prompt,
        #         campaign_id=campaign_id,
        #         lead_name=lead_name,
        #         voice=voice,
        #         organisation_id=organisation_id,
        #         user_id=user_id
        #     )
        if resolved_prov == "voicelink":
            from services.call_service_voicelink import call_initiate
            return call_initiate(
                to_number=to_number,
                agent_phone_number=agent_number,
                welcome_message=welcome_message,
                system_prompt=system_prompt,
                campaign_id=campaign_id,
                lead_name=lead_name,
                voice=voice,
                organisation_id=organisation_id,
                user_id=user_id
            )
        elif resolved_prov == "plivo":
            from services.call_service_plivo import call_initiate
            return call_initiate(
                to_number=to_number,
                agent_plivo_number=agent_number,
                welcome_message=welcome_message,
                system_prompt=system_prompt,
                campaign_id=campaign_id,
                lead_name=lead_name,
                voice=voice
            )
    except Exception as e:
        logger.error(f"[Scheduler-Dispatch] Error initiating call via {resolved_prov}: {e}")
        return False

def schedule_callback(
    campaign_id: str,
    to_number: str,
    run_at: datetime,
    provider: Optional[str] = None,
    from_number: Optional[str] = None,
    lead_name: Optional[str] = None
):
    """
    Schedule a callback for a specific future time, retaining the original provider and caller number.
    
    Args:
        campaign_id: The campaign identifier
        to_number: The phone number to call
        run_at: Datetime object when the call should happen
        provider: Provider name (frejun, voicelink, twilio, plivo)
        from_number: Agent / caller phone number
        lead_name: Name of the lead/customer for personalized greeting
    """
    # A naive datetime (LLM returned an IST time without a +05:30 offset) must be
    # interpreted as IST, not the server's local tz, or .timestamp() yields the
    # wrong epoch and the callback can appear immediately due.
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=IST)
        logger.info(f"[Scheduler] Naive run_at assumed to be IST: {run_at.isoformat()}")

    score = run_at.timestamp()
    now_ts = datetime.now(timezone.utc).timestamp()
    if score <= now_ts:
        logger.warning(
            f"[Scheduler] Callback time {run_at.isoformat()} is not in the future "
            f"(now={datetime.fromtimestamp(now_ts, IST).isoformat()}); it will fire immediately."
        )
    member_data = {
        "campaign_id": campaign_id,
        "to_number": to_number
    }
    if provider:
        member_data["provider"] = provider
    if from_number:
        member_data["from_number"] = from_number
    if lead_name:
        member_data["lead_name"] = lead_name

    member = json.dumps(member_data)
    # Add to Redis Sorted Set
    redis_client.zadd(SCHEDULE_KEY, {member: score})
    # Wake up the scheduler (in case it's sleeping for a later task)
    redis_client.publish(SCHEDULER_CHANNEL, "wake")
    logger.info(f"[Scheduler] Scheduled callback for {to_number} (Camp: {campaign_id}, Provider: {provider}, Lead: {lead_name}) at {run_at.isoformat()}")

    # Track that a callback has been scheduled for this campaign
    try:
        from services.campaign_tracking_service import get_campaign_tracking_service
        tracker = get_campaign_tracking_service()
        tracker.increment_progress(campaign_id, callbacks_scheduled_delta=1)
        logger.info(f"[Scheduler] Incremented callbacks_scheduled for campaign {campaign_id}")
    except Exception as track_err:
        logger.warning(f"[Scheduler] Failed to increment callbacks_scheduled for {campaign_id}: {track_err}")


def wait_for_wake(pubsub, timeout):
    """
    Blocking wait for a message on the pubsub channel or timeout.
    Returns: The message (dict) or None if timeout.
    """
    return pubsub.get_message(timeout=timeout)

async def run_scheduler_loop():
    """
    Smart background loop that sleeps until the next task is due or a new task is scheduled.
    Uses Redis PubSub to listen for "wake" events.
    """
    logger.info("[Scheduler] Starting smart scheduler loop...")
    
    # Create persistent PubSub connection
    pubsub = redis_client.pubsub()
    pubsub.subscribe(SCHEDULER_CHANNEL)
    # Consume potential subscription confirmation message
    pubsub.get_message(timeout=0.1)
    
    loop = asyncio.get_event_loop()
    
    while True:
        try:
            now_score = time.time()
            
            # 1. Check for due tasks (score <= current_timestamp)
            due_tasks = redis_client.zrangebyscore(SCHEDULE_KEY, 0, now_score)
            
            if due_tasks:
                logger.info(f"[Scheduler] Found {len(due_tasks)} due callbacks.")
                
                from services.call_service_voicelink import q_keys
                from services.campaign_tracking_service import get_campaign_tracking_service
                
                for member in due_tasks:
                    try:
                        # Remove from schedule immediately
                        if redis_client.zrem(SCHEDULE_KEY, member) == 0:
                            continue # Another worker took it
                            
                        task = json.loads(member)
                        campaign_id = task.get("campaign_id")
                        to_number = task.get("to_number")
                        provider = task.get("provider")
                        from_number = task.get("from_number")
                        lead_name = task.get("lead_name", "")
                        
                        if not campaign_id or not to_number:
                            continue
                            
                        logger.info(f"[Scheduler] Executing callback for {to_number} (Camp: {campaign_id}, Provider: {provider})")
                        
                        keys = q_keys(campaign_id)
                        
                        # Fetch Redis queue config
                        cfg_raw = redis_client.get(keys["config"])
                        cfg = json.loads(cfg_raw) if cfg_raw else {}
                        
                        # Fallback lookup if provider or from_number is missing
                        if not provider or not from_number:
                            provider = provider or cfg.get("provider")
                            from_number = from_number or cfg.get("full_phone_number")
                            
                            # Fallback to MongoDB calls collection
                            if not provider or not from_number:
                                try:
                                    from services.call_service_voicelink import calls_collection
                                    call_doc = calls_collection.find_one(
                                        {"campaign_id": campaign_id, "to_number": to_number},
                                        sort=[("_id", -1)]
                                    ) or calls_collection.find_one(
                                        {"campaign_id": campaign_id},
                                        sort=[("_id", -1)]
                                    )
                                    if call_doc:
                                        provider = provider or call_doc.get("provider")
                                        from_number = from_number or call_doc.get("from_number") or call_doc.get("agent_number") or call_doc.get("virtual_number")
                                except Exception as mongo_err:
                                    logger.warning(f"[Scheduler] Mongo lookup fallback failed: {mongo_err}")

                        resolved_prov = resolve_provider(from_number, provider)

                        tracker = get_campaign_tracking_service()

                        # Reopen campaign to 'running' so queue processor handles the call
                        try:
                            tracker.update_status(campaign_id, "running")
                            logger.info(f"[Scheduler] Reopened campaign {campaign_id} status to 'running'")
                        except Exception as stat_err:
                            logger.warning(f"[Scheduler] Failed to reopen status for {campaign_id}: {stat_err}")

                        # A callback is a brand-new call for this lead. Grow the campaign
                        # total by 1 so the denominator tracks the extra attempt; otherwise
                        # the new call's completion pushes completed past total (e.g. 4/3).
                        # With this, the retry reads correctly as 4/4. Guarded so the same
                        # scheduled callback can't inflate the total more than once.
                        total_bump_key = f"callback_total_bump:{campaign_id}:{to_number}:{int(now_score)}"
                        if redis_client.set(total_bump_key, "1", ex=86400, nx=True):
                            try:
                                tracker.increment_progress(
                                    campaign_id, 
                                    total_delta=1,
                                    callbacks_completed_delta=1
                                )
                                logger.info(
                                    f"[Scheduler] Grew campaign total by 1 and incremented callbacks_completed "
                                    f"for callback to {to_number} (Camp: {campaign_id})"
                                )
                            except Exception as bump_err:
                                logger.warning(f"[Scheduler] Total bump failed for {to_number}: {bump_err}")

                        # Push lead into queue as JSON object
                        lead_payload = json.dumps({"number": to_number, "name": lead_name})
                        redis_client.rpush(keys["queue"], lead_payload)

                        progress = tracker.get_progress(campaign_id)
                        
                        if progress.get("in_progress", 0) == 0:
                            next_item_raw = redis_client.lpop(keys["queue"])
                            if next_item_raw:
                                try:
                                    lead_obj = json.loads(next_item_raw)
                                    next_num = lead_obj.get("number") if isinstance(lead_obj, dict) else str(lead_obj)
                                    lead_name = lead_obj.get("name", "") if isinstance(lead_obj, dict) else ""
                                except Exception:
                                    next_num = next_item_raw
                                    lead_name = ""
                                    
                                agent_number = from_number or cfg.get("full_phone_number") or ""
                                welcome_msg = cfg.get("welcome_message")
                                sys_prompt = cfg.get("system_prompt")
                                voice = cfg.get("agent_voice") or cfg.get("allyroid_voice", "Autonoe")
                                org_id = cfg.get("organisation_id")
                                usr_id = cfg.get("user_id")
                                
                                await loop.run_in_executor(
                                    None,
                                    initiate_provider_call,
                                    resolved_prov,
                                    next_num,
                                    agent_number,
                                    welcome_msg,
                                    sys_prompt,
                                    campaign_id,
                                    lead_name,
                                    voice,
                                    org_id,
                                    usr_id
                                )
                                logger.info(f"[Scheduler] Kickstarted callback call via {resolved_prov.upper()} for {to_number}")
                                    
                    except Exception as e:
                        logger.error(f"[Scheduler] Error processing task {member}: {e}")
                
                # If we processed tasks, check again immediately (don't sleep)
                continue

            # 2. If NO due tasks, calculate how long to sleep
            earliest_task = redis_client.zrange(SCHEDULE_KEY, 0, 0, withscores=True)
            
            # Default sleep if queue is empty (e.g., 1 hour)
            wait_seconds = 3600 
            
            if earliest_task:
                run_at_score = earliest_task[0][1]
                wait_seconds = run_at_score - time.time()
                if wait_seconds < 0:
                    wait_seconds = 0
            
            # Ensure strictly positive wait for redis timeout (at least 100ms)
            if wait_seconds < 0.1:
                wait_seconds = 0.1
                
            logger.info(f"[Scheduler] Sleeping for {wait_seconds:.2f}s until next task or wake signal.")
            
            # 3. Blocking Wait: Wait for 'wait_seconds' OR until a 'wake' message arrives
            await loop.run_in_executor(None, wait_for_wake, pubsub, wait_seconds)

        except Exception as e:
            logger.error(f"[Scheduler] Loop error: {e}")
            await asyncio.sleep(5) 

def get_scheduled_tasks():
    """
    Retrieve all scheduled tasks from Redis.
    """
    tasks = redis_client.zrange(SCHEDULE_KEY, 0, -1, withscores=True)
    results = []
    for member, score in tasks:
         try:
             task_data = json.loads(member)
             task_data["scheduled_at"] = datetime.fromtimestamp(score).isoformat()
             results.append(task_data)
         except:
             continue
    return results

def delete_scheduled_task(campaign_id: str, to_number: str):
    """
    Remove a specific task from the schedule.
    """
    tasks = redis_client.zrange(SCHEDULE_KEY, 0, -1)
    deleted_count = 0
    for member in tasks:
        try:
            task_data = json.loads(member)
            if task_data.get("campaign_id") == campaign_id and task_data.get("to_number") == to_number:
                deleted_count += redis_client.zrem(SCHEDULE_KEY, member)
        except Exception:
            continue
    return deleted_count
