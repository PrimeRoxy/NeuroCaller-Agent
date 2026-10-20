"""
Simple mapping: Indian voice names <-> Gemini 3.1 Live Preview prebuilt voice names.
Source of truth: generate_gemini_live_samples.py
"""

# Indian name (lowercase) -> original Gemini prebuilt voice name
INDIAN_TO_GEMINI_VOICE = {
    "priya": "Zephyr",
    "arjun": "Puck",
    "vikram": "Charon",
    "ananya": "Kore",
    "rohan": "Fenrir",
    "kavya": "Leda",
    "aditya": "Orus",
    "meera": "Aoede",
    "nisha": "Callirrhoe",
    "diya": "Autonoe",
    "ishaan": "Enceladus",
    "ravi": "Iapetus",
    "sneha": "Umbriel",
    "karan": "Algieba",
    "pooja": "Despina",
    "riya": "Erinome",
    "deepak": "Algenib",
    "suresh": "Rasalgethi",
    "tara": "Laomedeia",
    "aarav": "Achernar",
    "rajesh": "Alnilam",
    "neha": "Schedar",
    "manoj": "Gacrux",
    "simran": "Pulcherrima",
    "sanjay": "Achird",
    "amit": "Zubenelgenubi",
    "anjali": "Vindemiatrix",
    "swati": "Sadachbia",
    "rahul": "Sadaltager",
    "divya": "Sulafat",
}

# Reverse: Gemini name (lowercase) -> canonical Gemini name
_GEMINI_CANONICAL = {v.lower(): v for v in INDIAN_TO_GEMINI_VOICE.values()}


def resolve_gemini_voice(voice_input, default="Aoede"):
    """
    Convert an Indian name or Gemini name to the exact prebuilt voice name.
    e.g. 'vikram' -> 'Charon', 'charon' -> 'Charon', None -> default
    """
    if not voice_input:
        return default
    v = str(voice_input).strip().lower()
    # Check Indian name first
    if v in INDIAN_TO_GEMINI_VOICE:
        return INDIAN_TO_GEMINI_VOICE[v]
    # Check if already a Gemini name
    if v in _GEMINI_CANONICAL:
        return _GEMINI_CANONICAL[v]
    return default
