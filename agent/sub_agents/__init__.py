"""
Sub-agents for the NeuroCaller parallel agent architecture.

- NumberValidatorAgent: Validates phone numbers in E.164 format and removes duplicates
- PromptGeneratorAgent: Creates dynamic prompts from FAQ content based on call intent
"""

from agent.sub_agents.number_validator import NumberValidatorAgent, validate_numbers
from agent.sub_agents.prompt_generator import PromptGeneratorAgent, generate_dynamic_prompt

__all__ = [
    "NumberValidatorAgent",
    "PromptGeneratorAgent",
    "validate_numbers",
    "generate_dynamic_prompt"
]
