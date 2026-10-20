# 🧠 NeuroCaller Agent

<div align="center">

![NeuroCaller Banner](https://img.shields.io/badge/NeuroCaller-AI%20Calling%20Agent-blueviolet?style=for-the-badge)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.133%2B-009688?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)
[![Google ADK](https://img.shields.io/badge/Google%20ADK-2.3-4285F4?style=for-the-badge&logo=google)](https://google.github.io/adk-docs/)
[![Gemini Live](https://img.shields.io/badge/Gemini-Live%20API-8E75B2?style=for-the-badge&logo=googlegemini)](https://ai.google.dev/gemini-api/docs/live)

**AI sales-calling agent: plan a campaign by chatting, let Gemini Live hold the calls, and watch every lead get scored, analysed, and followed up.**

[Overview](#-overview) • [Features](#-features) • [Architecture](#%EF%B8%8F-architecture) • [Installation](#-installation) • [Usage](#-usage) • [API](#-api-reference)

</div>

---

## 🌟 Overview

**NeuroCaller** is an outbound sales-calling platform built on [Google ADK](https://google.github.io/adk-docs/). You describe a campaign to the NeuroCaller agent in chat (script, product details, FAQs, and leads as text, a file, or Salesforce). The agent validates the numbers, builds a call prompt, and starts dialling. Every call is a real-time voice conversation run by the **Gemini Live API** over **Plivo** or **VoiceLink** telephony.

While a call is live, the caller is scored HOT / WARM / COLD and the result is streamed to the dashboard. After hangup, the recording is stored and analysed. Callbacks are scheduled, meetings booked, and the lead is synced to Salesforce.

The FastAPI backend also serves a web platform (no build step) with a command center, live monitor, campaign controls, agent studio, and a chat copilot.

### ⚡ Key Highlights

- 🤖 **Conversational campaign setup**: a Google ADK agent (`gemini-2.5-flash`) collects inputs, validates leads, generates the prompt, and launches the campaign from chat
- 🎙️ **Real-time voice with Gemini Live**: `gemini-3.1-flash-live-preview` holds the calls, with barge-in, a greeting gate, silence nudges, and model-initiated hangup
- 📞 **Two telephony providers**: Plivo (μ-law) worldwide and VoiceLink (A-law) for Indian numbers, picked automatically from the caller ID
- 🇮🇳 **India-ready voices and prompts**: 24 Indian-named voice previews mapped to Gemini Live voices, with Hinglish prompts for India and English elsewhere
- 🔥 **Live lead scoring**: caller turns are classified HOT / WARM / COLD during the call and pushed to the dashboard over WebSocket
- 📊 **Post-call analysis**: Gemini listens to the recording and extracts sales stage, next action, follow-up time, emotional tone, and the customer's email
- ⏰ **Callbacks and quiet hours**: follow-up times become scheduled redials, and running campaigns pause automatically when quiet hours begin
- ☁️ **Salesforce CRM**: pull leads to call, then push each call's outcome back to the Lead record

---

## 🎯 Features

### 🚀 Core Capabilities

| Feature | Description |
|---------|-------------|
| 💬 **Copilot chat** | Plan, launch, check, pause, resume, or cancel campaigns in plain language (`POST /salesmate`) |
| 📄 **File-aware inputs** | Upload scripts, product sheets, FAQs, or lead lists (PDF, DOCX, TXT, CSV…). They are passed to Gemini as files |
| ✅ **Lead validation** | E.164 normalisation and validation with `phonenumbers`, de-duplication, name/number parsing |
| 🧩 **Dynamic prompts** | Intent-aware prompt (sales, follow-up, cold call, appointment, information) built from the script, product details, and FAQs; every version is saved |
| 📱 **Campaign queue** | Leads go into a Redis queue and are dialled one at a time per campaign; separate campaigns run in parallel |
| 🎙️ **Live voice calls** | Bidirectional audio between the phone line and Gemini Live, with 8 kHz ⇄ 16/24 kHz transcoding and instant barge-in |
| 🔥 **Live lead status** | HOT / WARM / COLD with a one-line reason, updated as the customer speaks (VoiceLink calls) |
| 📼 **Recordings & transcripts** | Recordings copied to S3. Transcripts come from Plivo's transcription or Gemini Flash (VoiceLink) |
| 🧠 **Outcome analysis** | Sales stage (19 stages), next recommended action (12 actions), follow-up time, tone, confidence, background noise |
| 🔁 **Callbacks** | "Call me back in 15 minutes" becomes a scheduled redial that is dialled on time |
| 📅 **Meeting booking** | When a time and a confirmed email are captured, a one-hour meeting is booked through the external scheduler agent |
| 🌙 **Quiet hours** | New campaigns are blocked, and running ones paused, during the organisation's quiet hours (timezone-aware) |
| ☁️ **Salesforce** | Fetch open Leads with phone numbers, upsert call results by phone, check the connection, re-sync pending calls |
| 💰 **Cost tracking** | Per-call telephony and model-token cost, stored on the call record |

### 🛠️ Technical Stack

- **Agent framework**: Google ADK 2.3 (`LlmAgent` + tools), sessions stored in PostgreSQL via `DatabaseSessionService`
- **Voice AI**: Gemini Live API over raw WebSocket (`BidiGenerateContent`)
- **Analysis models**: Gemini 3.1 Flash Lite (post-call audio), Gemini 3.5 Flash Lite (live lead scoring), Gemini Flash (transcription, sub-agents)
- **Telephony**: Plivo (Voice API + bidirectional `<Stream>`), VoiceLink / Greeter dialer
- **Data**: MongoDB (calls, configs, campaigns, events, prompts), Redis (queues, live state, pub/sub, scheduler), AWS S3 (recordings)
- **Backend**: FastAPI + Uvicorn with background loops for the scheduler and quiet-hours watcher
- **Frontend**: plain HTML, CSS, and ES modules served from `utils/` (no build step)

---

## 🏗️ Architecture

```mermaid
flowchart TB
    UI["Web platform (/app)<br/>Copilot · Studio · Monitor"] -->|"chat + file"| API["POST /salesmate"]
    API --> AGENT["NeuroCaller agent<br/>Google ADK · gemini-2.5-flash"]
    AGENT --> TOOLS["Tools<br/>validate numbers · build prompt · quiet hours<br/>Salesforce leads · campaign status / pause / resume / cancel"]
    AGENT -->|"initiate_outbound_call"| QUEUE[("Redis<br/>call_queue:{campaign_id}")]
    QUEUE --> ROUTE{"Caller ID<br/>starts with +91?"}
    ROUTE -->|yes| VL["VoiceLink"]
    ROUTE -->|no| PL["Plivo"]
    VL <-->|"A-law 8 kHz"| WSVL["/media-stream-vl/"]
    PL <-->|"μ-law 8 kHz"| WSPL["/media-stream-plivo"]
    WSVL <--> LIVE["Gemini Live<br/>gemini-3.1-flash-live-preview"]
    WSPL <--> LIVE
    WSVL -.->|"customer turns"| LEAD["Live lead scoring<br/>HOT / WARM / COLD"]
    LEAD -.->|"Redis pub/sub"| WSLIVE["/ws/live-calls"]
    WSLIVE -.-> UI
    VL -->|"hangup webhook"| HANG["Hangup handler<br/>recording → S3 · dial next lead"]
    PL -->|"hangup webhook"| HANG
    HANG --> QUEUE
    HANG --> ANALYSIS["Post-call analysis<br/>Gemini on the recording"]
    ANALYSIS --> MONGO[("MongoDB<br/>calls")]
    ANALYSIS --> SCHED["Callback scheduler<br/>Redis sorted set"]
    ANALYSIS --> CRM["Salesforce sync"]
    ANALYSIS --> MEET["Meeting booking<br/>external scheduler"]
    SCHED --> QUEUE
```

### 📊 How a Campaign Runs

1. **🛠️ Configure the agent** (Agent studio → `POST /api/allyroid/config`)
   Agent name, company, voice, gender, caller-ID numbers, welcome message, timezone, working/quiet hours, CRM provider, and Salesforce credentials are saved per organisation in MongoDB `orgcalls`.

2. **💬 Plan in chat** (Copilot → `POST /salesmate`)
   The agent checks quiet hours first, then (best effort) whether the user's email is connected for meeting booking, and loads the org defaults. It then collects the call intent, sales script, product details/FAQs, caller ID, and leads (typed, uploaded, or fetched from Salesforce).

3. **✅ Validate and generate**
   `validate_numbers` normalises leads to E.164 and drops duplicates and invalid numbers. `generate_dynamic_prompt` builds the call prompt (Hinglish for India, English elsewhere) and saves it to `prompt_sessions`. The agent shows a preview and waits for confirmation.

4. **📞 Launch**
   `initiate_outbound_call` creates the campaign (MongoDB `campaigns` plus Redis status/progress), pushes the leads onto `call_queue:{campaign_id}`, and dials the first one. Caller IDs starting with `+91` go through **VoiceLink**; all others go through **Plivo**.

5. **🎙️ Talk**
   The provider opens a bidirectional media stream to this server, which bridges it to Gemini Live. The agent speaks the welcome message verbatim (with the lead's name filled in) and then follows the generated prompt. If the caller talks over it, queued audio is flushed. After 5 s of silence it checks in (up to 3 times). It ends the call by calling its `terminate_call` tool.

6. **📴 Hang up and continue**
   The hangup webhook stores duration and hangup cause, copies the recording to S3, and dials the next lead in the queue. When the queue is empty the campaign is marked `completed`.

7. **🧠 Analyse and follow up**
   After the transcript arrives, Gemini analyses the recording and writes the outcome to the call document. A follow-up time becomes a scheduled callback; a follow-up time plus a confirmed email books a meeting; with `crm_provider = salesforce`, the Lead is upserted.

8. **⏰ Background loops** (started with the app)
   The **scheduler** sleeps until the next callback is due and dials it. The **quiet-hours watcher** pauses running campaigns when an org's quiet hours begin.

---

## 📞 Telephony Providers

| | **Plivo** | **VoiceLink** |
|---|---|---|
| **Chosen when** | Caller ID does not start with `+91` | Caller ID starts with `+91` |
| **Dialling** | Plivo Voice API (`calls.create`) | VoiceLink add-lead API (lead pushed to the dialer hopper) |
| **Media stream** | `/media-stream-plivo` · μ-law 8 kHz | `/media-stream-vl/` · A-law 8 kHz |
| **Hangup webhook** | `POST /hangup-plivo` | `POST /hangup-vl` |
| **Transcript** | Plivo auto-transcription → `POST /action/transcription` | Gemini Flash transcribes the recording |
| **Live lead scoring** | – | ✅ |
| **Voice** | Indian name or Gemini voice name, mapped to a Gemini voice (default `Aoede`) | Org voice value passed to Gemini (default `Autonoe`) |
| **Direct test endpoint** | `POST /make-call-plivo` | `POST /make-call-vl`, `POST /quick-call` |

---

## 🤖 AI Models

| Role | Model | Configure with |
|------|-------|----------------|
| Orchestrator agent (chat) | `gemini-2.5-flash` | hard-coded in `agent/salesmate_agent.py` |
| Sub-agents (number validator, prompt generator) | `gemini-flash-latest` | `GEMINI_FLASH_MODEL` |
| Live voice calls | `gemini-3.1-flash-live-preview` | `GEMINI_LIVE_MODEL` |
| Live lead scoring | `gemini-3.5-flash-lite` | `LEAD_ANALYSIS_MODEL` |
| Recording transcription (VoiceLink) | `gemini-flash-latest` | hard-coded in `services/analysis_service.py` |
| Post-call outcome analysis | `gemini-3.1-flash-lite` | hard-coded in `sentiment/sentiment.py` |

### 🗣️ Voices

`GET /voices?country=IN` returns 24 Indian-named voices (Priya, Arjun, Vikram, Ananya…), each with a sample in `audio_samples/audio_samples_gemini_live/` and its Gemini voice ID (`services/voice_mapping.py`). Other countries return the older `alloy`, `ash`, `coral`… sample set.

---

## 🔧 Installation

### Prerequisites

- Python 3.11+ (the Docker image uses 3.11)
- MongoDB
- Redis
- PostgreSQL (stores ADK agent sessions)
- Google AI Studio API key with Gemini Live access
- A Plivo account and/or VoiceLink dialer credentials
- An AWS S3 bucket for recordings
- A public HTTPS URL for this server (for example ngrok in development), so Plivo/VoiceLink can reach the webhooks and media-stream WebSockets

### Quick Start

1. **Clone the repository**
```bash
git clone https://github.com/PrimeRoxy/NeuroCaller-Agent.git
cd NeuroCaller-Agent
```

2. **Create a virtual environment**
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Create `.env` in the project root**

```env
# ── Core ─────────────────────────────────────────────
MONGO_URI=mongodb://localhost:27017/neurocaller      # must include the database name
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_USERNAME=
REDIS_PASSWORD=
DB_URL=postgresql+asyncpg://user:pass@localhost:5432/neurocaller   # ADK session store
SERVER_BASE_URL=https://your-public-host              # Plivo stream URL + voice sample URLs

# ── Gemini ───────────────────────────────────────────
GOOGLE_API_KEY=your_gemini_key                        # or GEMINI_API_KEY
GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview
GEMINI_LIVE_VOICE=Autonoe
GEMINI_FLASH_MODEL=gemini-flash-latest
LEAD_ANALYSIS_MODEL=gemini-3.5-flash-lite
# Optional Live tuning: GEMINI_LIVE_SILENCE_MS=200, GEMINI_LIVE_PREFIX_PADDING_MS=300,
# GEMINI_LIVE_GREETING_GATE_S=5.0, GEMINI_LIVE_START_SENSITIVITY / _END_SENSITIVITY

# Post-call analysis is skipped when this is unset (the analysis itself runs on Gemini)
OPENAI_API_KEY=your_openai_key

# ── Plivo ────────────────────────────────────────────
PLIVO_AUTH_ID=your_plivo_auth_id
PLIVO_AUTH_TOKEN=your_plivo_auth_token
PLIVO_NUMBER=+1XXXXXXXXXX
PLIVO_ANSWER_URL=https://your-public-host/answer      # app appends /plivo
PLIVO_HANGUP_URL=https://your-public-host/hangup-plivo
PLIVO_ACTION_URL=https://your-public-host/action      # app appends /transcription

# ── VoiceLink (Indian caller IDs) ────────────────────
VOICELINK_TOKEN=your_voicelink_token
VOICELINK_NUMBER=91XXXXXXXXXX
VOICELINK_LIST_ID=your_list_id
VOICELINK_ADD_LEAD_URL=https://voicebroad.elisiontec.com/api/add-lead
WSS_URL=wss://your-public-host/media-stream-vl/
VOICELINK_HANGUP_URL=https://your-public-host/hangup-vl

# ── Recordings (S3) ──────────────────────────────────
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=your-bucket
S3_REGION=ap-south-1

# ── Meeting booking / email auth (external scheduler agent) ──
SCHEDULER_URL=https://your-scheduler-host
SALES_AGENT_ID=your_agent_id

# ── Optional ─────────────────────────────────────────
# PRODUCTION_URL=https://...           # when SERVER_BASE_URL matches, only VOICELINK_PRODUCTION_NUMBERS are offered
# VOICELINK_PRODUCTION_NUMBERS=91...,91...
# QUICK_CALL_VOICE / QUICK_CALL_WELCOME / QUICK_CALL_ORG_ID / QUICK_CALL_USER_ID
```

> Settings such as `SARVAM_*`, `SILERO_*`, `QDRANT_URL`, `TWILIO_*`, and `FREJUN_*` appear in `core/config.py` but no current code path reads them.

5. **Start the services**
```bash
# MongoDB, Redis and PostgreSQL must be running, then:
uvicorn main:app --reload --port 8000
```

| URL | What it is |
|-----|------------|
| `http://localhost:8000/` | Landing page |
| `http://localhost:8000/app` | NeuroCaller platform |
| `http://localhost:8000/docs` | Interactive API docs (Swagger) |
| `http://localhost:8000/health` | MongoDB + Redis health check |

---

## 📖 Usage

### 🌐 Web Platform

Open `http://localhost:8000/app`. It asks for an **organisation ID** and **user ID** (kept in the browser), or you can link straight in with `/app?org_id=…&user_id=…`.

1. **Agent studio**: set the agent's name, company, voice, caller-ID numbers, welcome message, hours, and Salesforce credentials
2. **Copilot**: tell the agent who to call and attach your script, product sheet, or lead list
3. **Live monitor**: watch active calls and their HOT / WARM / COLD status as they happen
4. **Campaigns / Calls / Leads / Analytics**: progress, recordings, transcripts, and outcomes

The frontend is plain HTML, CSS, and JavaScript modules in `utils/` (`index.html`, `app.html`, `assets/`), served by FastAPI with no build step. Only `utils/assets` is exposed as static files, so the Python modules in `utils/` stay private.

| Screen | Data source |
| --- | --- |
| Command center | `/api/dashboard/stats/summary`, `/calls/by-user-org`, `/api/dashboard/campaigns`, `/ws/live-calls` |
| Live monitor | `/ws/live-calls`, `/quick-call`, `/voices` |
| Campaigns | `/api/dashboard/campaign/{id}` and its `/progress`, `/calls`, `/events`, `/pause`, `/resume`, `/cancel`; `/api/scheduler/tasks` |
| Agent studio | `/api/allyroid/config`, `/voices`, `/rented-numbers`, `/api/salesforce/check` |
| Copilot | `/salesmate` |
| Calls, Leads, Analytics | `/calls/by-user-org`, `/api/dashboard/stats/summary` |
| Settings | `/health` |

### 🔌 Chat with the Agent

`POST /salesmate` (multipart form). Reuse `session_id` to continue a conversation; one file per message.

```bash
curl -X POST "http://localhost:8000/salesmate" \
  -F "query=Start a sales campaign for our new 2BHK project using this sheet" \
  -F "org_id=org_8f2c41" \
  -F "user_id=user_19ab07" \
  -F "files=@listings.pdf"
```

```json
{
  "message": "Got it. Which of these numbers should I call from? ...",
  "session_id": "session_3f1c...",
  "time_taken": 4.12,
  "token_details": { "input_tokens": 5120, "output_tokens": 210, "total_tokens": 5330 }
}
```

The agent can also answer *"How is my campaign going?"*, *"Pause the campaign"*, *"Call my Salesforce leads"*, or *"Am I authenticated?"*.

### ⚡ Quick Single Call

`POST /quick-call` dials one number through VoiceLink without going through chat. A 10-digit number gets `91` prepended.

```bash
curl -X POST "http://localhost:8000/quick-call" \
  -H "Content-Type: application/json" \
  -d '{"phone_number": "+919876543210", "lead_name": "Ravi", "voice": "Charon"}'
```

---

## 📚 API Reference

Full schemas are at `/docs`. Main routes:

| Area | Method & path | Purpose |
|------|---------------|---------|
| **Agent** | `POST /salesmate` | Chat with the NeuroCaller agent |
| **Config** | `POST /api/allyroid/config` | Create/update org agent config |
| | `GET` / `POST` / `DELETE /api/allyroid/config/{organisation_id}` | Read / update / delete config |
| | `GET /voices` | Voice catalog with sample URLs (`?country=IN`, `?gender=`) |
| | `GET /countries`, `GET /rented-numbers` | Available countries and caller-ID numbers |
| **Calls** | `GET /calls/by-user-org` | Call records for an org (optionally a user) |
| | `POST /quick-call` | One-off call via VoiceLink |
| | `POST /make-call-plivo`, `POST /make-call-vl` | Direct provider calls (testing) |
| **Dashboard** | `GET /api/dashboard/campaigns` | List campaigns (`org_id`, `user_id`, `status`) |
| | `GET /api/dashboard/campaign/{id}` | Campaign record + live progress |
| | `GET /api/dashboard/campaign/{id}/progress` \| `/calls` \| `/events` | Progress, call list, event history |
| | `POST /api/dashboard/campaign/{id}/pause` \| `/resume` \| `/cancel` | Campaign controls |
| | `GET /api/dashboard/stats/summary` | Totals, durations, hangup causes, follow-ups, per-member stats |
| **Scheduler** | `GET` / `DELETE /api/scheduler/tasks` | List or remove scheduled callbacks |
| **Salesforce** | `GET /api/salesforce/check` | Verify org credentials |
| | `GET /api/salesforce/sync-status/{organisation_id}` | Sync counts by status |
| | `POST /api/salesforce/sync-pending/{organisation_id}` | Re-sync pending/failed calls |
| | `POST /api/salesforce/sync` | Bulk upsert leads |
| **Live** | `WS /ws/live-calls?org_id=&user_id=` | `snapshot`, `update`, and `ping` messages for live calls |
| **Webhooks** | `POST /answer/plivo`, `POST /hangup-plivo`, `POST /action/transcription`, `POST /hangup-vl` | Provider callbacks |
| **Media** | `WS /media-stream-plivo`, `WS /media-stream-vl/` | Provider ⇄ Gemini Live audio bridges |
| **Health** | `GET /health` | MongoDB + Redis status |

---

## 💾 Data Storage

### MongoDB (database named in `MONGO_URI`)

| Collection | Contents |
|------------|----------|
| `orgcalls` | Per-org agent config: name, company, voice, numbers, welcome script, hours, timezone, CRM provider, Salesforce credentials |
| `campaigns` | Campaign status and counters (`total_numbers`, `completed`, `failed`, `in_progress`, `callbacks_*`) |
| `campaign_events` | Campaign/call lifecycle events (auto-expire after 7 days) |
| `calls` | One document per call (see below) |
| `prompt_sessions` | Every generated call prompt, with intent and metadata |

A `calls` document, abridged:

```javascript
{
  "call_uuid": "…",
  "campaign_id": "org_8f2c41:user_19ab07:20261020T091500Z",
  "organisation_id": "org_8f2c41",
  "user_id": "user_19ab07",
  "provider": "plivo",
  "from_number": "+1XXXXXXXXXX",
  "to_number": "+14155552671",
  "lead_name": "Alice",
  "start_time": "…", "end_time": "…", "duration": 184,
  "hangup_cause": "NORMAL_CLEARING",
  "recording_url": "https://<bucket>.s3.<region>.amazonaws.com/<id>.mp3",
  "transcript": "Agent: … Customer: …",

  // live lead scoring (VoiceLink)
  "lead_status": "HOT",
  "lead_reason": "Asked for pricing and a site visit.",
  "lead_status_history": [ … ],

  // post-call analysis
  "sales_stage": "Demo Scheduled",
  "next_recommended_action": "schedule_demo",
  "follow_up_required": true,
  "follow_up_date_time": "2026-10-21T11:00:00+05:30",
  "emotional_tone": "Interested",
  "confidence": "high",
  "extracted_email": "alice@example.com",
  "crm_sync": "synced",
  "analysis_token_metrics": { "provider_details": { … }, "total_price": 0.04 }
}
```

### Redis

| Key | Purpose |
|-----|---------|
| `call_queue:{campaign_id}` | Leads waiting to be dialled |
| `call_queue_config:{campaign_id}` / `call_config:{call_uuid}` | Prompt, welcome message, voice, and IDs for the campaign / call |
| `campaign:{id}:status`, `campaign:{id}:progress` | Live campaign state (7-day TTL) |
| `campaign:{id}:events` | Pub/sub channel for campaign events |
| `live_calls:{org_id}:{user_id}` | Pub/sub channel behind `/ws/live-calls` |
| `scheduled_callbacks` | Sorted set of due callbacks (score = epoch time) |
| `sf_token:{client_id}` | Cached Salesforce access token |

### PostgreSQL

Used only by Google ADK's `DatabaseSessionService` to keep chat sessions with the agent.

---

## 📁 Project Structure

```
├── main.py                     # FastAPI app, routers, static frontend, background loops
├── core/config.py              # Settings + shared Redis client
├── agent/
│   ├── salesmate_agent.py      # NeuroCaller ADK agent (orchestrator + instructions)
│   ├── tools.py                # Campaign, quiet-hours, Salesforce, auth tools; provider routing
│   └── sub_agents/             # Number validator, prompt generator
├── api/
│   ├── router.py               # /salesmate, config, voices, numbers, calls, scheduler
│   ├── dashboard_endpoints.py  # /api/dashboard/*
│   └── salesforce_router.py    # /api/salesforce/*
├── services/
│   ├── call_service_plivo.py   # Plivo campaigns, answer/hangup/transcription webhooks
│   ├── call_plivo.py           # Plivo ⇄ Gemini Live media bridge
│   ├── call_service_voicelink.py  # VoiceLink campaigns + hangup webhook
│   ├── call_gemini_live.py     # VoiceLink ⇄ Gemini Live media bridge
│   ├── analysis_service.py     # Transcription, post-call pipeline, CRM + meeting workers
│   ├── scheduler_service.py    # Callback scheduler loop
│   ├── quiet_hours_watcher.py  # Auto-pause during quiet hours
│   ├── campaign_tracking_service.py, task_event_service.py
│   ├── config_service.py, salesforce_service.py, usage_cost_tracker.py, …
├── sentiment/                  # Gemini post-call audio analysis
├── live_monitor/               # /ws/live-calls + live lead scoring
├── audio_samples/              # Voice previews
└── utils/                      # Frontend (index.html, app.html, assets/) + Plivo number helpers
```

---

## 🔐 Security

- 🔒 Secrets come from `.env` (git-ignored); at startup, values whose names look like keys, secrets, tokens, IDs, or URLs are logged masked.
- ⚠️ **The API and platform have no authentication.** The org and user IDs typed into `/app` are trusted as-is, and CORS allows all origins. Put the service behind your own auth gateway before exposing it.
- ⚠️ Salesforce client credentials are stored unencrypted in MongoDB `orgcalls`.
- ⚠️ `GET /api/prompt/check` is protected only by a hard-coded shared password. Change or remove it before deploying.

---

## 🚀 Deployment

### Docker

```bash
docker build -t neurocaller .
docker run --env-file .env -p 5000:5000 neurocaller
```

The image runs `uvicorn main:app --host 0.0.0.0 --port 5000`. MongoDB, Redis, and PostgreSQL are external. Set `SERVER_BASE_URL`, the Plivo `*_URL` values, `WSS_URL`, and `VOICELINK_HANGUP_URL` to the deployment's public HTTPS/WSS address.

---

## ⚠️ Known Limitations

- One live call at a time **per campaign**; there is no automatic retry for no-answer or busy calls. Only callbacks detected by the analysis are redialled.
- Live lead scoring runs on VoiceLink calls only.
- The config API does not save `country_code` yet, so the choice of Hinglish or English for a prompt comes from fallbacks rather than the org's country.
- The quiet-hours watcher pauses campaigns but does not resume them; resume from chat or the dashboard.
- Analysis, CRM-sync, and meeting jobs run on in-process queues and are lost if the server restarts mid-job.
- The non-India voice list (`alloy`, `ash`, …) consists of legacy samples. On Plivo calls these names fall back to the Gemini voice `Aoede`.
- Twilio and FreJun numbers appear in the number helpers and cost tables, but no calling code exists for them yet.

---

## 🛣️ Roadmap

- [x] CRM integration: Salesforce
- [x] Analytics dashboard
- [x] Live lead scoring during calls
- [x] Scheduled callbacks
- [ ] Authentication and multi-tenant access control
- [ ] Live lead scoring for Plivo calls
- [ ] Automatic retries for no-answer / busy
- [ ] Auto-resume after quiet hours
- [ ] Twilio and FreJun calling
- [ ] HubSpot integration
- [ ] A/B testing for call scripts
- [ ] WhatsApp/SMS fallback

---

## 🤝 Contributing

We welcome contributions!

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 👥 Authors

**Vipul Ray** - *Initial work* - [@PrimeRoxy](https://github.com/PrimeRoxy)

---

## 🙏 Acknowledgments

- Google for the Agent Development Kit and the Gemini & Gemini Live APIs
- Plivo and VoiceLink for voice infrastructure
- Salesforce for the CRM APIs
- The FastAPI community

---

## 📞 Support

- 📧 Email: vipuldashingboy@gmail.com

---

<div align="center">

**⭐ Star us on GitHub — it motivates us a lot!**

Made with ❤️ by the NeuroCaller team

</div>
