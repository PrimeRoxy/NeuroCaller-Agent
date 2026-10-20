
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
