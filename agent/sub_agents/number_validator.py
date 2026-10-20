import os
import re
from collections import Counter
from typing import List, Dict, Any, Union

import phonenumbers
from phonenumbers import NumberParseException
from google.adk.agents import LlmAgent


def normalize_phone_number(number: str) -> str:
    """
    Normalize a phone number by removing common formatting characters.
    Converts leading 00 to +.
    Does NOT blindly add + for plain digits, so invalid inputs can be reported properly.
    """
    cleaned = re.sub(r"[\s\-\(\)\.]", "", str(number).strip())

    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]

    return cleaned


def validate_phone_number_advanced(number: str) -> Dict[str, Any]:
    """
    Validate phone number using Google's libphonenumber.

    Rules:
    1. Must parse successfully
    2. Must include country code (international style)
    3. Must be a valid number for that country
    4. Returns normalized E.164 number if valid
    """
    cleaned = number.strip()

    if not cleaned:
        return {
            "valid": False,
            "reason": "Empty phone number"
        }

    if not cleaned.startswith("+"):
        return {
            "valid": False,
            "reason": "Missing country code or '+' prefix"
        }

    try:
        parsed = phonenumbers.parse(cleaned, None)

        if not phonenumbers.is_possible_number(parsed):
            return {
                "valid": False,
                "reason": "Number length is not possible for the country code"
            }

        if not phonenumbers.is_valid_number(parsed):
            return {
                "valid": False,
                "reason": "Invalid phone number for the given country code"
            }

        normalized = phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.E164
        )

        region = phonenumbers.region_code_for_number(parsed) or "Unknown"

        return {
            "valid": True,
            "normalized": normalized,
            "country": region
        }

    except NumberParseException as e:
        return {
            "valid": False,
            "reason": f"Parsing error: {str(e)}"
        }


async def validate_numbers(phone_numbers: List[Any]) -> Dict[str, Any]:
    """
    Validate and deduplicate phone numbers.

    Input can be:
    - list of strings
    - list of dicts with at least {"number": "..."} and optionally {"name": "..."}

    Returns:
    - valid_numbers: unique valid numbers, normalized to E.164
    - duplicates_removed: count of duplicate numbers removed
    - duplicated_numbers: normalized numbers that appeared more than once
    - invalid_numbers: invalid entries with reasons
    - original_count: original number count
    - valid_count: final valid unique number count
    """
    if not phone_numbers:
        return {
            "valid_numbers": [],
            "duplicates_removed": 0,
            "duplicated_numbers": [],
            "invalid_numbers": [],
            "original_count": 0,
            "valid_count": 0,
            "status": "success",
            "message": "No phone numbers provided"
        }

    processed_data = []

    for item in phone_numbers:
        if isinstance(item, dict):
            raw_num = item.get("number", "")
            name = item.get("name", "")
        else:
            raw_num = item
            name = ""

        normalized_input = normalize_phone_number(raw_num)
        validation_result = validate_phone_number_advanced(normalized_input)

        processed_data.append({
            "original": raw_num,
            "input_normalized": normalized_input,
            "name": name,
            "validation": validation_result
        })

    original_count = len(processed_data)

    # Count duplicates only among VALID normalized numbers
    valid_normalized_numbers = [
        entry["validation"]["normalized"]
        for entry in processed_data
        if entry["validation"]["valid"]
    ]
    number_counts = Counter(valid_normalized_numbers)

    valid_numbers = []
    invalid_numbers = []
    seen_valid_numbers = set()

    for entry in processed_data:
        original = entry["original"]
        name = entry["name"]
        result = entry["validation"]

        if result["valid"]:
            normalized = result["normalized"]

            if normalized in seen_valid_numbers:
                continue

            seen_valid_numbers.add(normalized)

            valid_numbers.append({
                "number": normalized,
                "name": name,
                "country": result.get("country", "")
            })
        else:
            invalid_numbers.append({
                "number": original,
                "reason": result["reason"]
            })

    duplicates_removed = len(valid_normalized_numbers) - len(seen_valid_numbers)
    duplicated_numbers = [
        num for num, count in number_counts.items() if count > 1
    ]

    return {
        "valid_numbers": valid_numbers,
        "duplicates_removed": duplicates_removed,
        "duplicated_numbers": duplicated_numbers,
        "invalid_numbers": invalid_numbers,
        "original_count": original_count,
        "valid_count": len(valid_numbers),
        "status": "success",
        "message": (
            f"Validated {len(valid_numbers)} unique numbers, "
            f"removed {duplicates_removed} duplicates, "
            f"found {len(invalid_numbers)} invalid"
        )
    }


NUMBER_VALIDATOR_INSTRUCTION = """
You are a specialized phone number validation agent. Your role is to:

1. Process lists of phone numbers provided by the user (which may include names).
2. Validate numbers in this order:
   - First check international format expectations
   - Then validate country code and number rules using libphonenumber
3. Normalize valid numbers into E.164 format
4. Remove duplicate phone numbers based on normalized number
5. Report validation results with names if present

Examples of valid normalized output:
- +14155552671
- +919876543210

## CRITICAL: Input Format Requirements
- Each item MUST be a dict: `{"name": "...", "number": "..."}`
- The `number` field must contain ONLY the phone number (with `+` prefix for international).
- The `name` field contains the contact name (can be empty string).
- **NEVER** pass a combined string like `"John: +919876543210"` as a phone number — the validator will reject it because it doesn't start with `+`.
- If you receive raw strings with names mixed in, FIRST separate the name from the number, THEN pass as a structured dict.

When given phone numbers:
- Call the validate_numbers tool with the list of phone numbers
- Report back:
  - How many valid unique numbers were found
  - How many duplicates were removed
  - Which numbers were invalid and why

You only handle phone number validation. Do not answer questions about other topics.
"""


class NumberValidatorAgent:
    """
    Agent 1: Number Validator

    This agent validates phone numbers and removes duplicates.
    It can be used as a sub-agent in the parallel agent architecture.
    """

    def __init__(self):
        gemini_model = os.getenv("GEMINI_FLASH_MODEL", "gemini-flash-latest")
        self.agent = LlmAgent(
            model=gemini_model,
            name="NumberValidator",
            description="Validates phone numbers using libphonenumber and removes duplicates.",
            tools=[validate_numbers],
            instruction=NUMBER_VALIDATOR_INSTRUCTION
        )

    async def validate(self, phone_numbers: List[Union[str, Dict[str, str]]]) -> Dict[str, Any]:
        """
        Directly validate phone numbers without going through the LLM.
        This is more efficient for programmatic use.
        """
        return await validate_numbers(phone_numbers)

    def get_agent(self) -> LlmAgent:
        """
        Get the underlying LlmAgent for use in agent composition.
        """
        return self.agent