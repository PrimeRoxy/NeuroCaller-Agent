import os
import json
import base64
import asyncio
import time
import logging
from contextlib import suppress
import aiohttp
import websockets
from dotenv import load_dotenv
from fastapi import APIRouter, WebSocket
from core.config import redis_client
from call.instruction import INSTRUCTIONS

load_dotenv()
router = APIRouter()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is not set. Please add it to your .env file")

SYSTEM_MESSAGE = INSTRUCTIONS  

# -------------------------
# Response/flow state
# -------------------------
ACTIVE_RESPONSE = False          # True after 'response.created', False after 'response.done'
PENDING_RESPONSE = False         # True after we send response.create, cleared on done/error
CREATE_LOCK = asyncio.Lock()     # Serializes response.create calls
CALL_SHOULD_TERMINATE = False   # Flag to indicate call termination requested
last_speech_stopped_ts_ms = 0.0  # for debounce
DEBOUNCE_MS = 350                # tune for your latency/noise

VAD_SETTINGS = {
    "threshold": 0.7,          # Higher = less sensitive to noise/echo
    "prefix_padding_ms": 2500, # Include audio before detection to avoid clipping
    "silence_duration_ms": 800 # Silence needed to mark end-of-turn
}

# -------------------------
# WebSocket endpoint
# -------------------------
@router.websocket("/media-stream")
async def handle_message(websocket: WebSocket):
    await websocket.accept()
    print("client connected")
    plivo_ws = websocket
    
    # Wait for the first message from Plivo which contains call metadata
    first_message = await plivo_ws.receive_text()
    first_data = json.loads(first_message)
    print(f"first_data: {first_data}")
    call_uuid = first_data.get("start", {}).get("callId")
    print(f"Received call UUID: {call_uuid}")
    
    # Fetch configuration from Redis
    welcome_message = None
    extracted_file_text = None
    
    if call_uuid:
        config_key = f"call_config:{call_uuid}"
        config_data = redis_client.get(config_key)
        if config_data:
            config = json.loads(config_data)
            welcome_message = config.get("welcome_message")
            extracted_file_text = config.get("extracted_file_text")
            print(f"Retrieved welcome message: {welcome_message}")

    # Realtime preview model. Keep in sync with OpenAI docs/releases.
    # url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
    url = "wss://api.openai.com/v1/realtime?model=gpt-realtime-2025-08-28"

    headers_list = [
        ("Authorization", f"Bearer {OPENAI_API_KEY}"),
        ("OpenAI-Beta", "realtime=v1"),
    ]

    try:
        # websockets >=14 uses `additional_headers`; 13.x uses `extra_headers`
        major = int(websockets.__version__.split(".", 1)[0])
        kwarg = {"additional_headers": headers_list} if major >= 14 else {"extra_headers": headers_list}

        async with websockets.connect(url, **kwarg) as openai_ws:
            print("connected to the OpenAI Realtime API")
            await realtime_loop(plivo_ws, openai_ws, welcome_message, extracted_file_text)

    except asyncio.CancelledError:
        print("client disconnected")
    except websockets.ConnectionClosed:
        print("Connection closed by OpenAI server")
    except Exception as e:
        print(f"Error during OpenAI's websocket communication: {e}")


# -------------------------
# Main realtime loop
# -------------------------
async def realtime_loop(plivo_ws: WebSocket, openai_ws, welcome_message=None, extracted_file_text=None):
    # Configure the session on OpenAI side
    await send_session_update(openai_ws,extracted_file_text)

    # Tiny cushion so VAD/session settle before greeting
    await asyncio.sleep(0.15)
    #  Use custom welcome message if available
    greeting = welcome_message or (
        "नमस्ते! मैं आपका रियल एस्टेट AI सलाहकार हूँ। "
        "मैं आपकी ज़रूरत, बजट और पसंद के अनुसार सबसे उपयुक्त घर या निवेश विकल्प ढूंढने में मदद करूँगा।"
    )

    # Initial spoken greeting (both audio + text)
    await maybe_create_response(
        openai_ws,
        instructions=(f"कृपया इस वाक्य को ज्यों का त्यों बोलें: {greeting}"),
    )

    # Start piping Plivo -> OpenAI and reading OpenAI -> Plivo
    receive_task = asyncio.create_task(receive_from_plivo(plivo_ws, openai_ws))

    async for message in openai_ws:
        # IMPORTANT: OpenAI Realtime can send either text JSON frames OR binary audio frames.
        if isinstance(message, (bytes, bytearray)):
            # Binary frames are raw μ-law bytes (because output_audio_format=g711_ulaw)
            # Plivo expects base64 μ-law in the 'payload' field.
            try:
                b64 = base64.b64encode(message).decode("ascii")
                await plivo_ws.send_json(
                    {
                        "event": "playAudio",
                        "media": {
                            "contentType": "audio/x-mulaw",
                            "sampleRate": 8000,
                            "payload": b64,
                        },
                    }
                )
            except Exception as e:
                print(f"Error forwarding binary audio to Plivo: {e}")
            continue

        # Otherwise it's a JSON text frame
        await receive_from_openai(message, plivo_ws, openai_ws)

    await receive_task


# -------------------------
# Plivo -> OpenAI
# -------------------------
async def receive_from_plivo(plivo_ws: WebSocket, openai_ws):
    """
    Receive events from Plivo and forward μ-law audio chunks to OpenAI.
    Use receive_json()/send_json() to avoid 'dict to json.loads' errors.
    """
    try:
        while True:
            data = await plivo_ws.receive_json()  # read JSON directly
            event = data.get("event")

            if event == "media":
                # Forward μ-law audio chunk from Plivo to OpenAI (base64-encoded)
                audio_payload_b64 = data["media"]["payload"]
                audio_append = {
                    "type": "input_audio_buffer.append",
                    "audio": audio_payload_b64,  # base64 μ-law
                }
                with suppress(websockets.ConnectionClosed):
                    await openai_ws.send(json.dumps(audio_append))

            elif event == "start":
                print("Plivo Audio stream has started")
                plivo_ws.stream_id = data.get("start", {}).get("streamId")

            elif event == "stop":
                print("Plivo stream stop received")
                with suppress(Exception):
                    await openai_ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                with suppress(Exception):
                    await openai_ws.close()
                with suppress(Exception):
                    await plivo_ws.close()
                break

            # (Handle any other Plivo events as needed)

    except websockets.ConnectionClosed:
        print("Connection closed for the plivo audio streaming servers")
        with suppress(Exception):
            await openai_ws.close()
    except Exception as e:
        print(f"Error during Plivo's websocket communication: {e}")


# -------------------------
# OpenAI -> Plivo
# -------------------------
async def receive_from_openai(message_text: str, plivo_ws: WebSocket, openai_ws):
    """
    Handle OpenAI JSON text events. Some transports may also send audio as base64 in JSON 'response.audio.delta';
    we support that too.
    """
    global ACTIVE_RESPONSE, PENDING_RESPONSE, last_speech_stopped_ts_ms

    try:
        response = json.loads(message_text)
        rtype = response.get("type")
        print("response received from OpenAI Realtime API: ", rtype)

        if rtype == "session.updated":
            print("session updated successfully")

        elif rtype == "error":
            # Clear pending if it's the "already has active response" error
            err = response.get("error", {}) or {}
            if err.get("code") == "conversation_already_has_active_response":
                PENDING_RESPONSE = False
            print("error received from realtime api: ", response)

        elif rtype == "response.created":
            ACTIVE_RESPONSE = True

        elif rtype == "response.done":
            ACTIVE_RESPONSE = False
            PENDING_RESPONSE = False
            
            print(f"🔍 Response done - checking for termination signal...")
            print(f"🔍 Response structure: {response}")
            
            # Check for function calls in the output array
            if 'response' in response and 'output' in response['response']:
                output_items = response['response']['output']
                for item in output_items:
                    if item.get('type') == 'function_call' and item.get('name') == 'terminate_call':
                        print(f"🔍 Terminate call function detected: {item}")
                        print("🔍 Initiating call termination...")
                        await asyncio.sleep(1)  # Brief pause
                        await handle_call_termination(plivo_ws, openai_ws)
                        return
            
            # Fallback: Check transcript for termination signal
            if 'response' in response and 'transcript' in response['response']:
                transcript = response['response']['transcript']
                print(f"🔍 AI transcript: '{transcript}'")
                
                # Check if transcript contains termination keywords
                termination_keywords = ['hangup', 'disconnect', 'goodbye', 'bye']
                transcript_lower = transcript.lower()
                has_termination_keyword = any(keyword in transcript_lower for keyword in termination_keywords)
                
                print(f"🔍 Checking for termination keywords: {termination_keywords}")
                print(f"🔍 Transcript lower: '{transcript_lower}'")
                print(f"🔍 Has termination keyword: {has_termination_keyword}")
                
                if has_termination_keyword:
                    print(f"🔍 Termination keyword detected in transcript: '{transcript}'")
                    print("🔍 Initiating call termination...")
                    await asyncio.sleep(2)
                    await handle_call_termination(plivo_ws, openai_ws)
                    return
                else:
                    print("🔍 No termination keywords found in transcript")
            else:
                print("🔍 No transcript found in response")
 

        elif rtype == "response.output_item.added":
            pass

        elif rtype == "conversation.item.created":
            pass

        elif rtype == "response.content_part.added":
            pass

        elif rtype == "response.audio_transcript.delta":
            # Optional: stream live transcript chars if you want to log them
            pass

        elif rtype == "response.audio.delta":
            # Some clients deliver audio as JSON base64 'delta'. Handle it for safety.
            audio_delta = {
                "event": "playAudio",
                "media": {
                    "contentType": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "payload": response["delta"],  # base64 μ-law from OpenAI
                },
            }
            await plivo_ws.send_json(audio_delta)

        elif rtype == "response.audio.done":
            # End of this audio response
            pass

        elif rtype == "input_audio_buffer.speech_started":
            # Barge-in: clear any queued audio and cancel current response
            print("speech started: clearing any queued audio and canceling current response")
            if hasattr(plivo_ws, "stream_id"):
                clear_audio_data = {
                    "event": "clearAudio",
                    "streamId": plivo_ws.stream_id,  # camelCase required by Plivo
                }
                await plivo_ws.send_json(clear_audio_data)

            if ACTIVE_RESPONSE or PENDING_RESPONSE:
                with suppress(Exception):
                    await openai_ws.send(json.dumps({"type": "response.cancel"}))

        elif rtype == "input_audio_buffer.speech_stopped":
            # Debounce to avoid rapid double-triggers
            now_ms = time.monotonic() * 1000.0
            if now_ms - last_speech_stopped_ts_ms > DEBOUNCE_MS:
                last_speech_stopped_ts_ms = now_ms
                await maybe_create_response(openai_ws)

        # (No tools or function-calls; removed for prompt-only behavior)
        elif rtype == "response.function_call":
            # ========================================
            # FUNCTION CALL HANDLING - ADDED
            # ========================================
            # Handle function calls
            print(f"🔍 Function call event: {response}")
            if response.get('function_call', {}).get('name') == 'terminate_call':
                print("🔍 Terminate call function called - initiating call termination")
                await asyncio.sleep(1)
                await handle_call_termination(plivo_ws, openai_ws)
                return
                
        elif rtype == "response.function_call_arguments.done":
            # Handle function call completion
            print(f"🔍 Function call arguments done: {response}")
            # Check if this is a terminate_call function
            if 'function_call' in response and response['function_call'].get('name') == 'terminate_call':
                print("🔍 Terminate call function completed - initiating call termination")
                await asyncio.sleep(1)
                await handle_call_termination(plivo_ws, openai_ws)
                return

    except Exception as e:
        print(f"Error handling OpenAI message: {e}")


# -------------------------
# Create response (guarded)
# -------------------------
async def maybe_create_response(openai_ws, instructions: str | None = None):
    """
    Create a model response only if one is not already active or pending.
    Guarded by a lock + flags to avoid 'conversation_already_has_active_response'.
    """
    global ACTIVE_RESPONSE, PENDING_RESPONSE

    async with CREATE_LOCK:
        if ACTIVE_RESPONSE or PENDING_RESPONSE:
            return

        payload = {
            "type": "response.create",
            "response": {
                "modalities": ["audio", "text"],
                "temperature": 0.7,
            },
        }
        if instructions:
            payload["response"]["instructions"] = instructions

        # Mark pending BEFORE sending to prevent races
        PENDING_RESPONSE = True
        await openai_ws.send(json.dumps(payload))
        # ACTIVE_RESPONSE will flip to True when we receive 'response.created'


# -------------------------
# Session config (prompt-only) + Call termination
# -------------------------
# ========================================
# CALL TERMINATION HANDLER - ADDED
# ========================================
async def handle_call_termination(plivo_ws, openai_ws):
    """Handle call termination by closing websocket connections."""
    global CALL_SHOULD_TERMINATE
    try:
        print("📞 Terminating call as requested by user...")
        
        # Close both connections properly
        print("📞 Closing connections...")
        
        # Close Plivo websocket connection first
        try:
            if hasattr(plivo_ws, 'close'):
                await plivo_ws.close(code=1000, reason="Call terminated")
                print("📞 Plivo websocket connection closed")
        except Exception as plivo_close_error:
            print(f"❌ Error closing Plivo websocket: {plivo_close_error}")
        
        # Close OpenAI connection
        try:
            await openai_ws.close()
            print("📞 OpenAI connection closed")
        except Exception as openai_close_error:
            print(f"❌ Error closing OpenAI connection: {openai_close_error}")
        
        print("📞 Call termination process completed")
        CALL_SHOULD_TERMINATE = False
        
    except Exception as e:
        print(f"❌ Error terminating call: {e}")
        import traceback
        traceback.print_exc()
async def send_session_update(openai_ws,extracted_file_text):
    """
    Configure the Realtime session: VAD, codecs, voice, system prompt, etc.
    Tools are removed; model answers from prompt knowledge only.
    """
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad",**VAD_SETTINGS,},
            

            # Match Plivo: μ-law at 8 kHz, both directions
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",

            "voice": "ash",
            # If you have a custom system prompt, set it here:
            "instructions": extracted_file_text or SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.8,

            # ========================================
            # TERMINATE_CALL FUNCTION - ADDED
            # ========================================
            # Add terminate_call function
            "tools": [
                {
                    "type": "function",
                    "name": "terminate_call",
                    "description": "Terminate the phone call when the user wants to end the conversation",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {
                                "type": "string",
                                "description": "Reason for terminating the call"
                            }
                        },
                        "required": ["reason"]
                    }
                }
            ],
        },
    }
    await openai_ws.send(json.dumps(session_update))
