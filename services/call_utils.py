import re

def personalize_system_prompt(system_prompt: str, lead_name: str) -> str:
    """Append lead context to system prompt if lead_name is available."""
    clean_name = (lead_name or "").strip()
    if not clean_name:
        return system_prompt
    lead_block = (
        f"\n\n---\n## LEAD INFORMATION\n"
        f"The lead's initial name on record is: {clean_name}.\n"
        f"- Address them by name ({clean_name}) naturally and sparingly when appropriate (do NOT repeat their name in every turn).\n"
        f"- Do NOT proactively ask for their name — you already have it on record.\n"
        f"- If the user/lead states that this is NOT their name or provides a different name during the call, immediately accept the correction, update the lead's name to the newly provided name, and address them by their corrected name for the rest of the call.\n"
        f"- The welcome/greeting message has ALREADY been spoken. Do NOT repeat it or introduce yourself again.\n"
    )
    return (system_prompt or "") + lead_block


# def personalize_welcome_message(welcome_message: str, lead_name: str) -> str:
#     """Interpolate {name}/{lead_name} placeholders in welcome message.

#     Args:
#         welcome_message: The welcome greeting template (may be None).
#         lead_name: The lead's name from chat/CRM (may be empty).

#     Returns:
#         The welcome message with placeholders replaced, or original if
#         lead_name is empty or welcome_message is None.
#     """
#     if not welcome_message:
#         return welcome_message
#     clean_name = (lead_name or "").strip()
#     if not clean_name or "{" not in welcome_message:
#         return welcome_message
#     return (welcome_message
#             .replace("{name}", clean_name)
#             .replace("{lead_name}", clean_name))

def personalize_welcome_message(welcome_message: str, lead_name: str) -> str:
    if not welcome_message:
        return welcome_message

    clean_name = (lead_name or "").strip()

    name_placeholder_pattern = re.compile(
        r"\{\{\s*[^{}]*(?:name|Name|NAME)[^{}]*\s*\}\}"
        r"|\{\s*[^{}]*(?:name|Name|NAME)[^{}]*\s*\}"
    )

    if clean_name:
        return name_placeholder_pattern.sub(clean_name, welcome_message)

    # No lead name -> remove the placeholder.
    result = name_placeholder_pattern.sub("", welcome_message)

    # Clean up spacing left behind by the removed placeholder.
    result = re.sub(r"[ \t]{2,}", " ", result)
    result = re.sub(r" +([,.!?])", r"\1", result)

    return result.strip()


# lead_name = ""
# welcome_message = "Hi, {{lead_name}}, how are you?"
# print(personalize_welcome_message(welcome_message, lead_name))