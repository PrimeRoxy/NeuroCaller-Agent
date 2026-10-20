"""
call_plivo.py - Plivo Media Streams + Gemini 3.1 Flash Live
============================================================

Supports:
  - Plivo Media Streams (callId, streamId, audio/x-mulaw;rate=8000)

Pipeline:
  Plivo Media Stream (μ-law 8 kHz) -> Gemini Live (PCM16 16 kHz) -> Gemini Live (PCM16 24 kHz) -> Plivo Media Stream (μ-law 8 kHz)

Dependencies:
    fastapi websockets python-dotenv redis certifi audioop-lts scipy numpy
"""

from __future__ import annotations
from scipy.signal import resample_poly
import numpy as np

import os
import json
import base64
import asyncio
import logging
import ssl
import time
from contextlib import suppress

import audioop  # type: ignore  (audioop-lts on Python >= 3.13)
import certifi
import websockets
from dotenv import load_dotenv
from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from core.config import redis_client
from services.call_utils import personalize_system_prompt, personalize_welcome_message
from services.instruction import INSTRUCTIONS
from services.voice_mapping import resolve_gemini_voice

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

# VAD / endpointing — owned by Live.
GEMINI_LIVE_SILENCE_MS = int(os.getenv("GEMINI_LIVE_SILENCE_MS", "200"))
GEMINI_LIVE_PREFIX_PADDING_MS = int(os.getenv("GEMINI_LIVE_PREFIX_PADDING_MS", "300"))
GEMINI_LIVE_START_SENSITIVITY = os.getenv(
    "GEMINI_LIVE_START_SENSITIVITY", "START_SENSITIVITY_LOW"
)
GEMINI_LIVE_END_SENSITIVITY = os.getenv(
    "GEMINI_LIVE_END_SENSITIVITY", "END_SENSITIVITY_LOW"
)

# Hold the caller's audio until the greeting starts, so ringback tail / "hello"
# doesn't read as barge-in and truncate the first sentence.
GREETING_GATE_MAX_S = float(os.getenv("GEMINI_LIVE_GREETING_GATE_S", "5.0"))

LIVE_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    f"?key={GEMINI_API_KEY}"
)

SYSTEM_MESSAGE = INSTRUCTIONS or (
    "You are a helpful voice assistant on a phone call. "
    "IMPORTANT RULES: "
    "Keep every reply to 2-3 sentences maximum. "
    "Never use bullet points, numbered lists, or markdown formatting. "
    "Speak naturally and conversationally as in a real phone call. "
    "Give direct, concise answers. "
    "Detect the language the caller is speaking (Hindi, English, Hinglish, or any other language) "
    "and respond in the SAME language. Switch languages dynamically if the caller switches. "
    "If the user says goodbye or wants to end the call, say a brief farewell, "
    "then call the terminate_call function."
)

# Audio rates.
PROVIDER_RATE = 8000       # Plivo μ-law telephony
LIVE_INPUT_RATE = 16000    # Gemini Live accepts PCM16 @ 16 kHz
LIVE_OUTPUT_RATE = 24000   # Gemini Live replies PCM16 @ 24 kHz
SAMPLE_WIDTH = 2           # 16-bit PCM
CHANNELS = 1

# Plivo TX pacing.
PLIVO_TX_CHUNK = 160         # 20 ms of μ-law @ 8 kHz
PLIVO_TX_BATCH = 5
PLIVO_TX_PACE_SEC = 0.018


# ─────────────────────────────────────────────────────────────────────────────
# Per-call state
# ─────────────────────────────────────────────────────────────────────────────

call_states: dict[str, dict] = {}


def get_call_state(call_uuid: str | None) -> dict:
    key = call_uuid or "__default__"

    if key not in call_states:
        call_states[key] = {
            "stream_id": "",
            "responding": False,
            "should_terminate": False,
            "user_speaking": False,
            "last_interaction_time": time.time(),
            "silence_check_count": 0,
            "agent_speech_end_time": 0.0,
            "first_audio_sent": False,
            "greeting_gate_until": 0.0,
            "rx_state": None,              # audioop.ratecv state, 8k -> 16k
            "tx_state": None,              # audioop.ratecv state, 24k -> 8k
            "tx_queue": None,              # asyncio.Queue of μ-law byte blobs
            "tx_generation": 0,            # bumped on barge-in to drop stale audio
        }

    return call_states[key]


def reset_call_state(call_uuid: str | None):
    state = get_call_state(call_uuid)
    state["responding"] = False
    state["should_terminate"] = False
    state["user_speaking"] = False
    state["last_interaction_time"] = time.time()
    state["silence_check_count"] = 0
    state["agent_speech_end_time"] = 0.0
    state["first_audio_sent"] = False
    state["greeting_gate_until"] = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Audio conversion helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_ssl_context():
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def provider_to_live(state: dict, mulaw_b64: str) -> str:
    """μ-law 8 kHz (base64) from Plivo -> PCM16 16 kHz (base64) for Gemini Live."""
    raw = base64.b64decode(mulaw_b64)
    if not raw:
        return ""
    pcm8 = audioop.ulaw2lin(raw, SAMPLE_WIDTH)
    pcm16, state["rx_state"] = audioop.ratecv(
        pcm8, SAMPLE_WIDTH, CHANNELS, PROVIDER_RATE, LIVE_INPUT_RATE, state["rx_state"]
    )
    return base64.b64encode(pcm16).decode("ascii")


def live_to_provider(state: dict, pcm24_b64: str) -> bytes:
    """PCM16 24 kHz from Gemini Live -> μ-law 8 kHz for Plivo."""
    pcm24 = base64.b64decode(pcm24_b64)
    if not pcm24:
        return b""

    samples = np.frombuffer(pcm24, dtype=np.int16)
    # 24 kHz -> 8 kHz with anti-alias filtering
    samples_8k = resample_poly(samples, 1, 3)
    pcm8 = samples_8k.astype(np.int16).tobytes()

    return audioop.lin2ulaw(pcm8, SAMPLE_WIDTH)


def iter_chunks(data: bytes, size: int):
    for i in range(0, len(data), size):
        yield data[i:i + size]


# ─────────────────────────────────────────────────────────────────────────────
# Plivo TX helpers
# ─────────────────────────────────────────────────────────────────────────────

async def send_media_to_plivo(ws: WebSocket, call_uuid: str | None, payload_b64: str):
    if not payload_b64:
        return

    message = {
        "event": "playAudio",
        "media": {
            "contentType": "audio/x-mulaw",
            "sampleRate": 8000,
            "payload": payload_b64,
        },
    }

    await ws.send_json(message)
    logger.info("[Media-Plivo] Sent audio delta to Plivo: payload_len=%s", len(payload_b64))


async def send_clear_to_plivo(ws: WebSocket, call_uuid: str | None):
    state = get_call_state(call_uuid)
    stream_id = state.get("stream_id") or getattr(ws, "stream_id", "")

    if not stream_id:
        return

    message = {
        "event": "clearAudio",
        "streamId": stream_id,
    }

    await ws.send_json(message)
    logger.info("[Media-Plivo] Sent clearAudio to Plivo: stream_id=%s", stream_id)


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


async def tx_sender(plivo_ws: WebSocket, call_uuid: str | None):
    """Pace μ-law out to Plivo; abortable mid-turn on barge-in."""
    state = get_call_state(call_uuid)
    q: asyncio.Queue = state["tx_queue"]
    try:
        while True:
            generation, mulaw = await q.get()
            # Stale audio from a turn that was interrupted; skip it.
            if generation != state["tx_generation"] or not mulaw:
                continue
            # Reserve playback time up front so terminate timing is honest.
            now = time.time()
            base = max(state.get("agent_speech_end_time", now), now)
            state["agent_speech_end_time"] = base + len(mulaw) / float(PROVIDER_RATE)
            for idx, chunk in enumerate(iter_chunks(mulaw, PLIVO_TX_CHUNK)):
                if generation != state["tx_generation"]:
                    break  # interrupted mid-turn
                with suppress(Exception):
                    payload_b64 = base64.b64encode(chunk).decode("ascii")
                    await send_media_to_plivo(plivo_ws, call_uuid, payload_b64)
                if idx and idx % PLIVO_TX_BATCH == 0:
                    await asyncio.sleep(PLIVO_TX_PACE_SEC * PLIVO_TX_BATCH)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception("[Plivo-TX] sender error: %s", exc)


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
                        "prebuiltVoiceConfig": {"voiceName": voice or "Aoede"}
                    },
                },
            },
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "realtimeInputConfig": {
                "automaticActivityDetection": {
                    "startOfSpeechSensitivity": GEMINI_LIVE_START_SENSITIVITY,
                    "endOfSpeechSensitivity": GEMINI_LIVE_END_SENSITIVITY,
                    "prefixPaddingMs": GEMINI_LIVE_PREFIX_PADDING_MS,
                    "silenceDurationMs": GEMINI_LIVE_SILENCE_MS,
                }
            },
            "systemInstruction": {
                "parts": [{"text": system_prompt or SYSTEM_MESSAGE}]
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
                                "'end the call', 'disconnect'), do NOT invoke this tool yet. First, ask for "
                                "confirmation (e.g., 'Should I go ahead and end the call now?').\n"
                                "2. If the user confirms ('yes', 'sure', 'ok'), speak the final farewell "
                                "AND invoke this function in that exact same turn.\n"
                                "3. If the user says no or has another query, continue the conversation "
                                "without calling this function."
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


def _parse(raw):
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


async def open_live(system_prompt: str | None, voice: str | None = None):
    """Connect and block until Gemini Live confirms the session (setupComplete).

    ``ping_interval=None`` because Google's BidiGenerateContent does not answer
    WebSocket pings; the default keepalive kills the session mid-call.
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
    logger.info("[Live-Plivo] session ready (model=%s voice=%s)", GEMINI_LIVE_MODEL, voice or "Aoede")
    return live


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
    logger.info("[Live-Plivo] greeting turn sent")


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.websocket("/media-stream-plivo")
async def handle_message(websocket: WebSocket):
    call_uuid = None

    try:
        requested_protocols = websocket.headers.get("sec-websocket-protocol")
        if requested_protocols:
            protocol = requested_protocols.split(",")[0].strip()
            await websocket.accept(subprotocol=protocol)
        else:
            await websocket.accept()

        logger.info("[WS-Plivo] Plivo Client connected (Gemini Live pipeline)")
        plivo_ws = websocket

        welcome_message = None
        system_prompt = None
        voice = "Aoede"  # Default Gemini voice

        try:
            first_message = await plivo_ws.receive_text()
            first_data = json.loads(first_message)
            logger.info("[WS-Plivo] First event: %s", first_data)
        except WebSocketDisconnect:
            logger.warning("[WS-Plivo] Disconnected before start event")
            return
        except Exception as exc:
            logger.exception("[WS-Plivo] Failed to parse initial media event: %s", exc)
            return

        start_data = first_data.get("start", {})
        call_uuid = start_data.get("callId") or start_data.get("callSid") or first_data.get("callId")
        stream_id = start_data.get("streamId") or first_data.get("streamId")

        logger.info("[WS-Plivo] call_uuid=%s stream_id=%s", call_uuid, stream_id)

        if stream_id:
            setattr(plivo_ws, "stream_id", stream_id)

        state = get_call_state(call_uuid)
        if stream_id:
            state["stream_id"] = stream_id

        if call_uuid:
            with suppress(Exception):
                await asyncio.to_thread(
                    redis_client.setex,
                    f"provider_call:{call_uuid}",
                    7200,
                    call_uuid,
                )
                await asyncio.to_thread(
                    redis_client.setex,
                    "last_active_call_uuid",
                    7200,
                    call_uuid,
                )

            try:
                config_key = f"call_config:{call_uuid}"
                config_data = await asyncio.to_thread(redis_client.get, config_key)
                if config_data:
                    if isinstance(config_data, bytes):
                        config_data = config_data.decode("utf-8")
                    config = json.loads(config_data)
                    welcome_message = config.get("welcome_message")
                    system_prompt = config.get("system_prompt")
                    lead_name = config.get("lead_name", "")

                    # Resolve voice: support Indian names, Gemini names, or fallback
                    raw_voice = config.get("voice") or config.get("allyroid_voice") or "Aoede"
                    voice = resolve_gemini_voice(raw_voice, default="Aoede")

                    # Personalize prompt & greeting with lead's name
                    system_prompt = personalize_system_prompt(system_prompt, lead_name)
                    welcome_message = personalize_welcome_message(welcome_message, lead_name)

                    logger.info(
                        "[Redis-Plivo] Config loaded: welcome=%s prompt=%s voice=%s lead=%s",
                        bool(welcome_message),
                        bool(system_prompt),
                        voice,
                        lead_name or "(none)",
                    )
                else:
                    logger.warning("[Redis-Plivo] No config found in Redis for call_uuid: %s", call_uuid)
            except Exception as redis_error:
                logger.exception("[Redis-Plivo] Redis error loading config: %s", redis_error)

        reset_call_state(call_uuid)

        # Open Gemini Live.
        try:
            live = await open_live(system_prompt, voice)
        except Exception as exc:
            logger.exception("[Live-Plivo] could not open the session: %s", exc)
            with suppress(Exception):
                await plivo_ws.close()
            return

        try:
            async with live:
                await realtime_loop(
                    plivo_ws=plivo_ws,
                    live=live,
                    welcome_message=welcome_message,
                    call_uuid=call_uuid,
                )
        except asyncio.CancelledError:
            logger.info("[WS-Plivo] Client disconnected")
        except websockets.ConnectionClosed as exc:
            logger.warning("[Live-Plivo] Connection closed: code=%s reason=%s", exc.code, exc.reason)
        except Exception as exc:
            logger.exception("[Live-Plivo] WebSocket error: %s", exc)

    except Exception as exc:
        logger.exception("[WS-Plivo] Handler error: %s", exc)

    finally:
        if call_uuid and call_uuid in call_states:
            del call_states[call_uuid]
            logger.info("[WS-Plivo] Cleaned state for call %s", call_uuid)



# ─────────────────────────────────────────────────────────────────────────────
# Main realtime loop
# ─────────────────────────────────────────────────────────────────────────────

async def realtime_loop(
    plivo_ws: WebSocket,
    live,
    welcome_message=None,
    call_uuid=None,
):
    state = get_call_state(call_uuid)
    state["tx_queue"] = asyncio.Queue()

    # Start the caller-audio gate: hold inbound until the greeting begins.
    state["greeting_gate_until"] = time.time() + GREETING_GATE_MAX_S

    sender_task = asyncio.create_task(tx_sender(plivo_ws, call_uuid))
    receive_task = asyncio.create_task(receive_from_plivo(plivo_ws, live, call_uuid))
    silence_task = asyncio.create_task(silence_monitor(live, call_uuid))

    greeting = welcome_message or (
        "Hello! I'm your AI assistant calling to assist you. "
        "How can I help you today?"
    )

    logger.info("[Greeting-Plivo] Creating initial greeting: %s", greeting[:100])
    await send_greeting(live, greeting)

    try:
        async for message in live:
            parsed = _parse(message)
            if parsed is None:
                continue
            await receive_from_live(parsed, plivo_ws, live, call_uuid)
            if state.get("should_terminate") and state.get("_termination_started"):
                pass
    finally:
        for t in (receive_task, silence_task, sender_task):
            t.cancel()
        for t in (receive_task, silence_task, sender_task):
            with suppress(asyncio.CancelledError):
                await t


# ─────────────────────────────────────────────────────────────────────────────
# Plivo -> Gemini Live
# ─────────────────────────────────────────────────────────────────────────────

async def receive_from_plivo(plivo_ws: WebSocket, live, call_uuid=None):
    state = get_call_state(call_uuid)
    try:
        while True:
            try:
                message = await plivo_ws.receive_text()
                data = json.loads(message)
                event = data.get("event")

                if event == "media":
                    audio_payload_b64 = data.get("media", {}).get("payload", "")
                    if not audio_payload_b64:
                        continue

                    # Greeting gate: withhold caller audio until the agent's first
                    # audio is on its way, so ringback / "hello" doesn't cut the greeting.
                    if not state.get("first_audio_sent"):
                        gate = state.get("greeting_gate_until", 0.0)
                        if gate and time.time() < gate:
                            continue

                    with suppress(Exception):
                        pcm16_b64 = provider_to_live(state, audio_payload_b64)
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
                    start_data = data.get("start", {})
                    stream_id = start_data.get("streamId") or data.get("streamId")
                    if stream_id:
                        setattr(plivo_ws, "stream_id", stream_id)
                        state["stream_id"] = stream_id
                        logger.info("[Plivo] Captured stream_id: %s", stream_id)

                elif event == "stop":
                    logger.info("[Plivo] Stream stop received")
                    with suppress(Exception):
                        await live.close()
                    with suppress(Exception):
                        await plivo_ws.close()
                    break

            except WebSocketDisconnect:
                logger.info("[Plivo] WebSocket disconnected")
                break
            except RuntimeError as exc:
                if "not connected" in str(exc).lower() or "accept" in str(exc).lower():
                    break
                raise
            except websockets.ConnectionClosed:
                logger.info("[Plivo] Gemini Live socket closed")
                break
            except Exception as exc:
                logger.exception("[Plivo] Error processing message: %s", exc)
                break

    finally:
        with suppress(Exception):
            await live.close()


# ─────────────────────────────────────────────────────────────────────────────
# Gemini Live -> Plivo
# ─────────────────────────────────────────────────────────────────────────────

async def receive_from_live(message: dict, plivo_ws: WebSocket, live, call_uuid=None):
    state = get_call_state(call_uuid)

    if "error" in message:
        logger.error("[Live-Plivo] %s", json.dumps(message["error"])[:300])
        return

    # Tool calls (terminate_call).
    tool_call = message.get("toolCall") or {}
    for call in tool_call.get("functionCalls") or []:
        name = call.get("name")
        logger.info("[Live-Plivo] tool call: %s", name)
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
            asyncio.create_task(terminate_after_audio(plivo_ws, live, call_uuid))

    content = message.get("serverContent") or {}

    # Caller transcript — logging only.
    heard = (content.get("inputTranscription") or {}).get("text")
    if heard:
        state["user_speaking"] = True
        state["last_interaction_time"] = time.time()
        logger.info("[Live-Plivo] caller: %s", heard)

    said = (content.get("outputTranscription") or {}).get("text")
    parts = (content.get("modelTurn") or {}).get("parts") or []

    if (said or parts) and not state.get("responding"):
        state["responding"] = True
        state["last_interaction_time"] = time.time()
    if said:
        logger.info("[Live-Plivo] agent: %s", said)

    # Agent audio -> convert 24k PCM -> 8k μ-law -> queue to Plivo.
    for part in parts:
        data = (part.get("inlineData") or {}).get("data")
        if not data:
            continue
        with suppress(Exception):
            mulaw = live_to_provider(state, data)
            if mulaw:
                if not state.get("first_audio_sent"):
                    state["first_audio_sent"] = True
                    state["greeting_gate_until"] = 0.0
                    logger.info("[Live-Plivo] greeting/first audio started; caller audio released")
                q = state.get("tx_queue")
                if q is not None:
                    q.put_nowait((state["tx_generation"], mulaw))

    # Barge-in: Live decided the caller took over.
    if content.get("interrupted"):
        logger.info("[Live-Plivo] interruption detected (barge-in)")
        flush_tx(call_uuid)
        await send_clear_to_plivo(plivo_ws, call_uuid)
        state["responding"] = False

    if content.get("turnComplete") or content.get("generationComplete"):
        state["responding"] = False
        state["last_interaction_time"] = time.time()
        state["user_speaking"] = False


# ─────────────────────────────────────────────────────────────────────────────
# Silence monitor
# ─────────────────────────────────────────────────────────────────────────────

async def silence_monitor(live, call_uuid=None):
    try:
        while True:
            await asyncio.sleep(1.0)

            call_state = get_call_state(call_uuid)

            if call_state.get("should_terminate"):
                break

            now = time.time()

            if (
                call_state.get("responding")
                or call_state.get("user_speaking")
                or call_state.get("agent_speech_end_time", 0.0) > now
            ):
                call_state["last_interaction_time"] = now
                continue

            if now - call_state.get("last_interaction_time", now) >= 5.0:
                count = call_state.get("silence_check_count", 0)

                if count < 3:
                    logger.info("[Silence-Plivo] User quiet, prompting once")
                    call_state["silence_check_count"] = count + 1
                    call_state["last_interaction_time"] = time.time()

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
        logger.exception("[Silence-Plivo] Error: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Termination helpers
# ─────────────────────────────────────────────────────────────────────────────

async def terminate_after_audio(plivo_ws: WebSocket, live, call_uuid=None):
    state = get_call_state(call_uuid)
    if state.get("_termination_started"):
        return
    state["_termination_started"] = True
    state["should_terminate"] = True

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
    logger.info("[Terminate-Plivo] waited %.2fs for playback before closing", time.time() - started)

    with suppress(Exception):
        await live.close()
    with suppress(Exception):
        if hasattr(plivo_ws, "close"):
            await plivo_ws.close(code=1000, reason="Call terminated")

    logger.info("[Terminate-Plivo] Call closed: %s", call_uuid)
