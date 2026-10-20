from __future__ import annotations
from scipy.signal import resample_poly
import numpy as np

import os
import json
import ssl
import time
import base64
import asyncio
import logging
from contextlib import suppress

import audioop  # type: ignore  (audioop-lts on Python >= 3.13)
import certifi
import websockets
from dotenv import load_dotenv
from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from core.config import redis_client
from services.call_utils import personalize_system_prompt, personalize_welcome_message
from services.instruction import OUTBOUND_SALES_SCRIPT

from live_monitor.live_lead_analysis_service import initialize_live_call,schedule_lead_analysis,stop_live_analysis

load_dotenv()

router = APIRouter()
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set in .env")

GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
GEMINI_LIVE_VOICE = os.getenv("GEMINI_LIVE_VOICE", "Autonoe")
GEMINI_LIVE_LANGUAGE = os.getenv("GEMINI_LIVE_LANGUAGE", "hi-IN")

# VAD / endpointing — owned by Live. Identical defaults to the s2s pipeline.
GEMINI_LIVE_SILENCE_MS = int(os.getenv("GEMINI_LIVE_SILENCE_MS", "200"))
GEMINI_LIVE_PREFIX_PADDING_MS = int(os.getenv("GEMINI_LIVE_PREFIX_PADDING_MS", "300"))
GEMINI_LIVE_START_SENSITIVITY = os.getenv(
    "GEMINI_LIVE_START_SENSITIVITY", "START_SENSITIVITY_LOW"
)
GEMINI_LIVE_END_SENSITIVITY = os.getenv(
    "GEMINI_LIVE_END_SENSITIVITY", "END_SENSITIVITY_LOW"
)

# Hold the caller's audio until the greeting starts, so ringback tail / "hello"
# doesn't read as barge-in and truncate the first sentence. Bounded so a greeting
# that never arrives costs a moment, not the call.
GREETING_GATE_MAX_S = float(os.getenv("GEMINI_LIVE_GREETING_GATE_S", "5.0"))

LIVE_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    f"?key={GEMINI_API_KEY}"
)



# Rates.
PROVIDER_RATE = 8000       # VoiceLink A-law telephony
LIVE_INPUT_RATE = 16000    # Gemini Live accepts PCM16 @ 16 kHz
LIVE_OUTPUT_RATE = 24000   # Gemini Live replies PCM16 @ 24 kHz
SAMPLE_WIDTH = 2           # 16-bit PCM
CHANNELS = 1

# VoiceLink TX pacing (mirrors call_voicelink.py).
VOICELINK_TX_CHUNK = 160         # 20 ms of A-law @ 8 kHz
VOICELINK_TX_BATCH = 5
VOICELINK_TX_PACE_SEC = 0.018


# ─────────────────────────────────────────────────────────────────────────────
# Per-call state
# ─────────────────────────────────────────────────────────────────────────────

call_states: dict[str, dict] = {}


def get_call_state(call_uuid: str | None) -> dict:
    key = call_uuid or "__default__"
    if key not in call_states:
        call_states[key] = {
            "stream_id": "",
            "responding": False,          # agent is currently producing a turn
            "should_terminate": False,
            "user_speaking": False,
            "last_interaction_time": time.time(),
            "silence_check_count": 0,
            "agent_speech_end_time": 0.0,  # wall-clock when queued audio finishes
            "first_audio_sent": False,     # first agent audio of the call has begun
            "greeting_gate_until": 0.0,
            "rx_state": None,              # audioop.ratecv state, 8k -> 16k
            "tx_state": None,              # audioop.ratecv state, 24k -> 8k
            "tx_queue": None,              # asyncio.Queue of A-law byte blobs
            "tx_generation": 0,            # bumped on barge-in to drop stale audio

            # Live lead intelligence (never blocks the audio path)
            "customer_buffer": "",
            "conversation_history": [],
            "lead_analysis_debounce_task": None,
            "lead_analysis_tasks": set(),
        }
    return call_states[key]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_ssl_context():
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def parse_json_maybe(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def extract_stream_id(first_data: dict, start_data: dict) -> str:
    return (
        first_data.get("streamSid")
        or first_data.get("stream_sid")
        or start_data.get("streamSid")
        or start_data.get("stream_sid")
        or start_data.get("streamId")
        or start_data.get("stream_id")
        or ""
    )


def extract_call_id(first_data: dict, start_data: dict) -> str:
    return (
        start_data.get("callSid")
        or start_data.get("callId")
        or start_data.get("call_sid")
        or first_data.get("callSid")
        or ""
    )


def extract_custom_params(start_data: dict) -> dict:
    raw = start_data.get("customParameters") or start_data.get("custom_parameters") or {}
    if isinstance(raw, dict):
        value = raw.get("value", raw)
        return parse_json_maybe(value) if isinstance(value, str) else value
    if isinstance(raw, str):
        return parse_json_maybe(raw)
    return {}


# ── audio conversion ─────────────────────────────────────────────────────────

def provider_to_live(state: dict, alaw_b64: str) -> str:
    """A-law 8 kHz (base64) from VoiceLink -> PCM16 16 kHz (base64) for Live."""
    raw = base64.b64decode(alaw_b64)
    if not raw:
        return ""
    pcm8 = audioop.alaw2lin(raw, SAMPLE_WIDTH)
    pcm16, state["rx_state"] = audioop.ratecv(
        pcm8, SAMPLE_WIDTH, CHANNELS, PROVIDER_RATE, LIVE_INPUT_RATE, state["rx_state"]
    )
    return base64.b64encode(pcm16).decode("ascii")


# def live_to_provider(state: dict, pcm24_b64: str) -> bytes:
#     """PCM16 24 kHz (base64) from Live -> A-law 8 kHz (raw bytes) for VoiceLink."""
#     pcm24 = base64.b64decode(pcm24_b64)
#     if not pcm24:
#         return b""
#     pcm8, state["tx_state"] = audioop.ratecv(
#         pcm24, SAMPLE_WIDTH, CHANNELS, LIVE_OUTPUT_RATE, PROVIDER_RATE, state["tx_state"]
#     )
#     return audioop.lin2alaw(pcm8, SAMPLE_WIDTH)

def live_to_provider(state: dict, pcm24_b64: str) -> bytes:
    """PCM16 24 kHz from Gemini -> A-law 8 kHz for VoiceLink."""
    pcm24 = base64.b64decode(pcm24_b64)
 
    if not pcm24:
        return b""
 
    samples = np.frombuffer(pcm24, dtype=np.int16)
 
    # 24 kHz -> 8 kHz with anti-alias filtering
    samples_8k = resample_poly(samples, 1, 3)
 
    pcm8 = samples_8k.astype(np.int16).tobytes()
 
    return audioop.lin2alaw(pcm8, SAMPLE_WIDTH)


def iter_chunks(data: bytes, size: int):
    for i in range(0, len(data), size):
        yield data[i:i + size]


# ── VoiceLink TX ─────────────────────────────────────────────────────────────

async def send_clear_to_provider(ws: WebSocket, call_uuid: str | None):
    state = get_call_state(call_uuid)
    stream_id = state.get("stream_id", "")
    if not stream_id:
        return
    with suppress(Exception):
        await ws.send_json({"event": "clear", "streamSid": stream_id})
        logger.info("[VL-TX] clear sent")


def flush_tx(call_uuid: str | None):
    """Drop any audio queued for an abandoned turn (barge-in)."""
    state = get_call_state(call_uuid)
    state["tx_generation"] += 1
    q = state.get("tx_queue")
    if q is not None:
        while not q.empty():
            with suppress(asyncio.QueueEmpty):
                q.get_nowait()
    state["agent_speech_end_time"] = 0.0


async def tx_sender(media_ws: WebSocket, call_uuid: str | None):
    """Pace A-law out to VoiceLink; abortable mid-turn on barge-in."""
    state = get_call_state(call_uuid)
    q: asyncio.Queue = state["tx_queue"]
    try:
        while True:
            generation, alaw = await q.get()
            # Stale audio from a turn that was interrupted; skip it.
            if generation != state["tx_generation"] or not alaw:
                continue
            stream_id = state.get("stream_id", "")
            if not stream_id:
                continue
            # Reserve playback time up front so terminate timing is honest.
            now = time.time()
            base = max(state.get("agent_speech_end_time", now), now)
            state["agent_speech_end_time"] = base + len(alaw) / float(PROVIDER_RATE)
            for idx, chunk in enumerate(iter_chunks(alaw, VOICELINK_TX_CHUNK)):
                if generation != state["tx_generation"]:
                    break  # interrupted mid-turn
                with suppress(Exception):
                    await media_ws.send_json({
                        "event": "media",
                        "streamSid": stream_id,
                        "media": {"payload": base64.b64encode(chunk).decode("ascii")},
                    })
                if idx and idx % VOICELINK_TX_BATCH == 0:
                    await asyncio.sleep(VOICELINK_TX_PACE_SEC * VOICELINK_TX_BATCH)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception("[VL-TX] sender error: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Gemini Live setup
# ─────────────────────────────────────────────────────────────────────────────

def build_setup_payload(system_prompt: str | None, voice: str | None = None) -> dict:
    return {
        "setup": {
            "model": f"models/{GEMINI_LIVE_MODEL}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice or GEMINI_LIVE_VOICE}
                    },
                    # "languageCode": GEMINI_LIVE_LANGUAGE,
                },
            },
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            # The VAD. Live owns endpointing and generates replies itself.
            "realtimeInputConfig": {
                "automaticActivityDetection": {
                    "startOfSpeechSensitivity": GEMINI_LIVE_START_SENSITIVITY,
                    "endOfSpeechSensitivity": GEMINI_LIVE_END_SENSITIVITY,
                    "prefixPaddingMs": GEMINI_LIVE_PREFIX_PADDING_MS,
                    "silenceDurationMs": GEMINI_LIVE_SILENCE_MS,
                }
            },
            "systemInstruction": {
                "parts": [{"text": system_prompt or OUTBOUND_SALES_SCRIPT}]
            },
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": "terminate_call",
                            "description": (
                                "Terminates the live call session. Call this tool ONLY in the final turn "
                                "alongside the concluding farewell message, AFTER the user has explicitly "
                                "confirmed they are ready to end the call.\n\n"
                                "Protocol:\n"
                                "1. When intent to disconnect or exit is detected (e.g., 'bye', 'hang up', "
                                "'phone rakh do', 'call kaat do', 'baad mein baat karte hain'), do NOT invoke "
                                "this tool yet. First, ask for confirmation (e.g., 'Kya main abhi call disconnect kar sakta hoon?' "
                                "or 'Should I go ahead and end the call now?').\n"
                                "2. If the user confirms ('haan', 'yes', 'sure', 'theek hai', 'ok'), speak the final farewell "
                                "(e.g., 'Thank you for your time, have a great day!') AND invoke this function in that exact same turn.\n"
                                "3. If the user says no or has another query, continue the conversation without calling this function."
                            ),
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "reason": {
                                        "type": "string",
                                        "description": "Optional reason for terminating.",
                                    }
                                },
                            },
                        }
                    ]
                }
            ],
        }
    }


async def open_live(system_prompt: str | None, voice: str | None = None):
    """Connect and block until Live confirms the session (setupComplete).

    ``ping_interval=None`` because Google's BidiGenerateContent does not answer
    WebSocket pings; the default keepalive kills the session mid-call. The audio
    stream is the liveness signal.
    """
    live = await websockets.connect(
        LIVE_WS_URL,
        ssl=make_ssl_context(),
        open_timeout=10,
        max_size=None,
        ping_interval=None,
    )
    await live.send(json.dumps(build_setup_payload(system_prompt, voice)))

    async with asyncio.timeout(10):
        while True:
            msg = _parse(await live.recv())
            if msg is None:
                continue
            if "setupComplete" in msg:
                break
            if "error" in msg:
                await live.close()
                raise RuntimeError(f"Gemini Live rejected the session: {msg['error']}")
    logger.info("[Live] session ready (model=%s voice=%s lang=%s)",GEMINI_LIVE_MODEL, voice or GEMINI_LIVE_VOICE, GEMINI_LIVE_LANGUAGE)
    return live


def _parse(raw):
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


async def send_greeting(live, greeting: str):
    """Kick off the opening turn. Live speaks it, then listens."""
    await live.send(json.dumps({
        "clientContent": {
            "turns": [{
                "role": "user",
                "parts": [{"text": f"Say this to the caller, verbatim: {greeting}"}],
            }],
            "turnComplete": True,
        }
    }))
    logger.info("[Live] greeting turn sent")


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.websocket("/media-stream-vl/")
async def handle_message_gemini_live(websocket: WebSocket):
    call_uuid = None
    try:
        requested = websocket.headers.get("sec-websocket-protocol")
        if requested:
            await websocket.accept(subprotocol=requested.split(",")[0].strip())
        else:
            await websocket.accept()
        logger.info("[WS] VoiceLink media stream connected (Gemini Live pipeline)")

        media_ws = websocket

        # Read past the optional "connected" frame to the "start".
        first_data = {}
        try:
            first_data = json.loads(await media_ws.receive_text())
            if first_data.get("event") == "connected":
                first_data = json.loads(await media_ws.receive_text())
        except WebSocketDisconnect:
            logger.warning("[WS] Disconnected before start event")
            return
        except Exception as exc:
            logger.exception("[WS] Failed to parse initial event: %s", exc)
            return

        start_data = first_data.get("start", {})
        provider_call_id = extract_call_id(first_data, start_data)
        stream_id = extract_stream_id(first_data, start_data)
        custom_params = extract_custom_params(start_data)
        call_uuid = custom_params.get("call_uuid") or provider_call_id
        from_number = start_data.get("to")
        to_number = start_data.get("from")

        logger.info(
            "[WS] call_uuid=%s provider_call_id=%s stream_id=%s",
            call_uuid, provider_call_id, stream_id,
        )

        state = get_call_state(call_uuid)
        state["stream_id"] = stream_id

        # Load per-call config (welcome + system prompt + lead name) from Redis.
        welcome_message = None
        system_prompt = None
        voice= GEMINI_LIVE_VOICE
        if call_uuid:
            with suppress(Exception):
                await asyncio.to_thread(
                    redis_client.setex, f"provider_call:{provider_call_id}", 7200, call_uuid
                )
            try:
                config_data = await asyncio.to_thread(
                    redis_client.get, f"call_config:{call_uuid}"
                )
                if config_data:
                    if isinstance(config_data, bytes):
                        config_data = config_data.decode("utf-8")
                    config = json.loads(config_data)
                    welcome_message = config.get("welcome_message")
                    system_prompt = config.get("system_prompt")
                    lead_name = config.get("lead_name", "")
                    voice = config.get("allyroid_voice") or GEMINI_LIVE_VOICE
                    system_prompt = personalize_system_prompt(system_prompt, lead_name)
                    welcome_message = personalize_welcome_message(welcome_message, lead_name)
                    logger.info("[Redis] Config loaded: welcome=%s prompt=%s lead=%s voice=%s", bool(welcome_message), bool(system_prompt), lead_name or "(none)", voice)
                else:
                    logger.warning("[Redis] No config for call_config:%s", call_uuid)
            except Exception as exc:
                logger.exception("[Redis] Error loading config: %s", exc)

        # Initialize/update the SAME MongoDB call document. This runs in the
        # background and does not block Gemini Live audio processing.
        asyncio.create_task(
            initialize_live_call(
                call_uuid=call_uuid,
                from_number=from_number,
                to_number=to_number,
            )
        )

        # Open Gemini Live.
        try:
            live = await open_live(system_prompt, voice)
        except Exception as exc:
            logger.exception("[Live] could not open the session: %s", exc)
            with suppress(Exception):
                await media_ws.close()
            return

        try:
            async with live:
                await realtime_loop(media_ws, live, welcome_message, call_uuid)
        except asyncio.CancelledError:
            logger.info("[WS] Task cancelled")
        except websockets.ConnectionClosed as exc:
            logger.warning("[Live] Connection closed: code=%s reason=%s", exc.code, exc.reason)
        except Exception as exc:
            logger.exception("[Live] WebSocket error: %s", exc)

    except Exception as exc:
        logger.exception("[WS] Handler error: %s", exc)
    finally:
        if call_uuid and call_uuid in call_states:
            state = call_states[call_uuid]

            debounce_task = state.get("lead_analysis_debounce_task")
            if debounce_task and not debounce_task.done():
                debounce_task.cancel()

            # for analysis_task in list(state.get("lead_analysis_tasks", set())):
            #     if not analysis_task.done():
            #         analysis_task.cancel()

            # Prevent any late analysis task from changing the live status.
            with suppress(Exception):
                await stop_live_analysis(call_uuid)

            del call_states[call_uuid]
            logger.info("[WS] Cleaned state for call %s", call_uuid)


# ─────────────────────────────────────────────────────────────────────────────
# Main bridge loop
# ─────────────────────────────────────────────────────────────────────────────

async def realtime_loop(media_ws: WebSocket, live, welcome_message, call_uuid):
    state = get_call_state(call_uuid)
    state["tx_queue"] = asyncio.Queue()

    # Start the caller-audio gate: hold inbound until the greeting begins.
    state["greeting_gate_until"] = time.time() + GREETING_GATE_MAX_S

    sender_task = asyncio.create_task(tx_sender(media_ws, call_uuid))
    recv_task = asyncio.create_task(receive_from_provider(media_ws, live, call_uuid))
    silence_task = asyncio.create_task(silence_monitor(live, call_uuid))

    greeting = welcome_message or "Namaste! Main Sally bol rahi hoon, Tata Neu se. Main aaj aapki kaise help kar sakti hoon?"
    await send_greeting(live, greeting)

    try:
        async for message in live:
            parsed = _parse(message)
            if parsed is None:
                continue
            await receive_from_live(parsed, media_ws, live, call_uuid)
            if state.get("should_terminate") and state.get("_termination_started"):
                # termination handler drains audio then closes sockets
                pass
    finally:
        for t in (recv_task, silence_task, sender_task):
            t.cancel()
        for t in (recv_task, silence_task, sender_task):
            with suppress(asyncio.CancelledError):
                await t


# ── VoiceLink -> Live ────────────────────────────────────────────────────────

async def receive_from_provider(media_ws: WebSocket, live, call_uuid=None):
    state = get_call_state(call_uuid)
    try:
        while True:
            try:
                data = json.loads(await media_ws.receive_text())
            except WebSocketDisconnect:
                logger.info("[VL] WebSocket disconnected")
                state["should_terminate"] = True
                with suppress(Exception):
                    await live.close()
                break

            event = data.get("event")

            if event == "media":
                payload = data.get("media", {}).get("payload", "")
                if not payload:
                    continue

                # Greeting gate: withhold caller audio until the agent's first
                # audio is on its way, so ringback / "hello" doesn't cut the
                # greeting. Bounded, and only before the first agent audio.
                if not state.get("first_audio_sent"):
                    gate = state.get("greeting_gate_until", 0.0)
                    if gate and time.time() < gate:
                        continue

                with suppress(Exception):
                    pcm16_b64 = provider_to_live(state, payload)
                    if pcm16_b64:
                        await live.send(json.dumps({
                            "realtimeInput": {
                                "audio": {
                                    "data": pcm16_b64,
                                    "mimeType": f"audio/pcm;rate={LIVE_INPUT_RATE}",
                                }
                            }
                        }))

            elif event == "start":
                sid = extract_stream_id(data, data.get("start", {}))
                if sid:
                    state["stream_id"] = sid
                logger.info("[VL] start event: stream_id=%s", state.get("stream_id"))

            elif event == "connected":
                logger.info("[VL] connected event")

            elif event == "stop":
                logger.info("[VL] stop event")
                with suppress(Exception):
                    await live.close()
                break

    except Exception as exc:
        logger.exception("[VL] Error processing message: %s", exc)


# ── Live -> VoiceLink ────────────────────────────────────────────────────────

async def receive_from_live(message: dict, media_ws: WebSocket, live, call_uuid=None):
    state = get_call_state(call_uuid)

    if "error" in message:
        logger.error("[Live] %s", json.dumps(message["error"])[:300])
        return

    # Tool calls (terminate_call).
    tool_call = message.get("toolCall") or {}
    for call in tool_call.get("functionCalls") or []:
        name = call.get("name")
        logger.info("[Live] tool call: %s", name)
        # Answer the tool so Live isn't left waiting.
        with suppress(Exception):
            await live.send(json.dumps({
                "toolResponse": {
                    "functionResponses": [{
                        "id": call.get("id"),
                        "name": name,
                        "response": {"result": "ok"},
                    }]
                }
            }))
        if name == "terminate_call":
            asyncio.create_task(terminate_after_audio(media_ws, live, call_uuid))

    content = message.get("serverContent") or {}

    # Caller transcript — logging / interruption evidence only. Never fed back
    # as a user turn; the model already heard the audio.
    heard = (content.get("inputTranscription") or {}).get("text")
    if heard:
        state["user_speaking"] = True
        state["last_interaction_time"] = time.time()
        logger.info("[Live] caller: %s", heard)

        # Buffer transcript fragments. The analysis scheduler debounces them
        # so one customer turn produces one background evaluation instead of
        # one LLM call per transcript chunk.
        state["customer_buffer"] = (
            state.get("customer_buffer", "") + " " + heard
        ).strip()

        schedule_lead_analysis(
            call_uuid=call_uuid,
            state=state,
        )

    said = (content.get("outputTranscription") or {}).get("text")
    parts = (content.get("modelTurn") or {}).get("parts") or []

    if (said or parts) and not state.get("responding"):
        state["responding"] = True
        state["last_interaction_time"] = time.time()
    if said:
        logger.info("[Live] agent: %s", said)

    # Agent audio -> convert 24k PCM -> 8k A-law -> queue to VoiceLink.
    for part in parts:
        data = (part.get("inlineData") or {}).get("data")
        if not data:
            continue
        with suppress(Exception):
            alaw = live_to_provider(state, data)
            if alaw:
                if not state.get("first_audio_sent"):
                    state["first_audio_sent"] = True
                    state["greeting_gate_until"] = 0.0
                    logger.info("[Live] greeting/first audio started; caller audio released")
                q = state.get("tx_queue")
                if q is not None:
                    q.put_nowait((state["tx_generation"], alaw))

    # Barge-in: Live decided the caller took over. Drop queued agent audio and
    # tell VoiceLink to stop playing at once.
    if content.get("interrupted"):
        logger.info("[Live] interruption detected (barge-in)")
        flush_tx(call_uuid)
        await send_clear_to_provider(media_ws, call_uuid)
        state["responding"] = False

    if content.get("turnComplete") or content.get("generationComplete"):
        state["responding"] = False
        state["last_interaction_time"] = time.time()
        state["user_speaking"] = False


# ─────────────────────────────────────────────────────────────────────────────
# Silence monitor — nudge the caller once if they go quiet
# ─────────────────────────────────────────────────────────────────────────────

async def silence_monitor(live, call_uuid=None):
    try:
        while True:
            await asyncio.sleep(1.0)
            state = get_call_state(call_uuid)
            if state.get("should_terminate"):
                break

            now = time.time()
            if (
                state.get("responding")
                or state.get("user_speaking")
                or state.get("agent_speech_end_time", 0.0) > now
            ):
                state["last_interaction_time"] = now
                continue

            if now - state.get("last_interaction_time", now) >= 5.0:
                if state.get("silence_check_count", 0) < 3:
                    state["silence_check_count"] += 1
                    state["last_interaction_time"] = now
                    logger.info("[Silence] caller quiet, nudging once")
                    with suppress(Exception):
                        await live.send(json.dumps({
                            "clientContent": {
                                "turns": [{
                                    "role": "user",
                                    "parts": [{"text": (
                                        "(The caller has been silent for a few seconds. "
                                        "Say a short, natural check-in like asking if "
                                        "they are still there.)"
                                    )}],
                                }],
                                "turnComplete": True,
                            }
                        }))
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception("[Silence] Error: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Termination
# ─────────────────────────────────────────────────────────────────────────────

async def terminate_after_audio(media_ws: WebSocket, live, call_uuid=None):
    state = get_call_state(call_uuid)
    if state.get("_termination_started"):
        return
    state["_termination_started"] = True
    state["should_terminate"] = True

    # Don't snapshot the wait: when terminate_call lands, most of the farewell
    # is still in tx_queue, which agent_speech_end_time doesn't cover yet.
    # Keep checking until the queue drains and playback catches up (max 8s).
    started = time.time()
    deadline = started + 8.0
    q = state.get("tx_queue")
    while time.time() < deadline and (
        (q is not None and not q.empty())
        or state.get("agent_speech_end_time", 0.0) > time.time()
    ):
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.0)
    logger.info("[Terminate] waited %.2fs for playback before closing", time.time() - started)

    with suppress(Exception):
        stream_id = state.get("stream_id", "")
        if stream_id:
            await media_ws.send_json({"event": "stop", "streamSid": stream_id})
    with suppress(Exception):
        await live.close()
    with suppress(Exception):
        await media_ws.close(code=1000, reason="Call terminated")
    logger.info("[Terminate] call closed: %s", call_uuid)
