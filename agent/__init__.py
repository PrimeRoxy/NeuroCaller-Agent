"""
NeuroCaller Agent Package

LLM-centric sales calling agent that uses Gemini for:
- Intent detection
- Phone number extraction
- Script/FAQ understanding  
- Conversational input validation

Python tools only for actual API calls (Plivo).
"""

from agent.salesmate_agent import SalesmateAgent
from agent.tools import initiate_outbound_call

__all__ = [
    "SalesmateAgent",
    "initiate_outbound_call"
]
