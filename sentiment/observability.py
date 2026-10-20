import json, os, asyncio
from google import genai
from pydantic import ValidationError
from google.genai import types
from dotenv import load_dotenv
import logging
from pydantic import BaseModel, ConfigDict, Field
from pydantic import BaseModel
from typing import Optional


class AudioPricing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_price_audio: float = Field(ge=0)
    input_price_text: float = Field(ge=0)
    caching_price_audio: float = Field(ge=0)
    caching_price_text: float = Field(ge=0)
    output_price: float = Field(ge=0)



WEB_SEARCH_MODEL_PRICE = """
Search the official pricing documentation for this audio model:

{model}

Only use the model provider's official pricing documentation.

Return ONLY a JSON object in exactly this format:

{{
    "input_audio_price": 0.0,
    "cached_audio_price": 0.0,
    "output_audio_price": 0.0
}}

Rules:
- All prices must be USD per 1 million tokens.
- input_audio_price = audio input token price.
- cached_audio_price = cached audio input token price.
- output_audio_price = audio output token price.
- If a category does not exist for the model, return 0.0.
- Use only the official model provider's pricing documentation.
- Do not use third-party pricing pages.
- Do not return explanations, Markdown, or code fences.
- Return ONLY the JSON object.
"""

load_dotenv()

# Token usage + cost tracking for LLM calls (Gemini ADK events, Gemini API, OpenAI Realtime).
# Usage flow: Observability() -> record(...) per API response -> await calculate_cost(model) -> metrics()



# Hardcoded per-token prices (USD) for known Gemini models.
# Values are already divided by 1_000_000 so they can be multiplied directly by token counts.
# Keys must match the exact model name passed to calculate_cost() (e.g. "gemini-3.6-flash").
logger = logging.getLogger(__name__)

# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("Observability")

MODEL_PRICING = {
    "gemini-3.6-flash": {
        "input_price": 1.50 / 1_000_000,
        "output_price": 7.50 / 1_000_000,
        "caching_price": 0.15 / 1_000_000,
    },
    "gemini-3.5-flash": {
        "input_price": 1.50 / 1_000_000,
        "output_price": 9.00 / 1_000_000,
        "caching_price": 0.15 / 1_000_000,
    },
    "gemini-2.5-flash": {
        "input_price": 1.0 / 1_000_000,
        "caching_price": 0.10 / 1_000_000,
        "output_price": 2.50 / 1_000_000,
    },
    "gemini-3.1-flash-lite":{
        "input_price_audio": 0.50 / 1_000_000,
        "input_price_text": 0.25 / 1_000_000,
        "caching_price_audio": 0.05 / 1_000_000,
        "caching_price_text": 0.025 / 1_000_000,
        "output_price": 1.50 / 1_000_000
    },
    "gemini-2.5-flash-lite":{
        "input_price": 0.30 / 1_000_000,
        "caching_price": 0.03 / 1_000_000,
        "output_price": 0.40 / 1_000_000
    }
}

# Gemini client used only by search_model_pricing() when a model is not in MODEL_PRICING.
# Requires GEMINI_API_KEY in the environment.
API = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=API)

async def search_model_pricing(model: str):
    response = await asyncio.to_thread(
        client.models.generate_content,
        model="gemini-3.1-flash-lite-preview",
        contents=WEB_SEARCH_MODEL_PRICE.format(model=model),
        config=types.GenerateContentConfig(
            tools=[
                types.Tool(
                    google_search=types.GoogleSearch()
                )
            ],
            response_mime_type="application/json",
        ),
    )

    pricing = json.loads(response.text)

    return {
        "input_price": pricing["input_audio_price"] / 1_000_000,
        "caching_price": pricing["cached_audio_price"] / 1_000_000,
        "output_price": pricing["output_audio_price"] / 1_000_000,
    }


async def get_model_pricing(model):
    """
    Resolve pricing for a model name.
    1. Return from MODEL_PRICING if the model is known locally (fast path).
    2. Otherwise call search_model_pricing() to fetch pricing from the web.
    Returns dict with keys: input_price, output_price, caching_price (each = USD per token).
    Raises ValueError if pricing cannot be resolved.
    """
    # Check local pricing
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]

    # Model not found → search internet
    pricing = await search_model_pricing(model)

    if pricing is None:
        raise ValueError(f"Pricing not found for model: {model}")

    return pricing


class Observability:
    def __init__(self,model="."):
        self.model = model
        self.audio_input_tokens = 0
        self.text_input_tokens = 0
        self.output_tokens = 0
        self.thought_tokens = 0
        self.cached_tokens_audio = 0
        self.cached_tokens_text = 0
        self.total_tokens = 0

        self.input_token_cost_audio = 0
        self.input_token_cost_text = 0
        self.output_token_cost = 0
        self.cache_token_cost_audio = 0
        self.cache_token_cost_text = 0
        self.total_cost_usd = 0

    def record(self, usage):
        if usage is None:
            return

        self.output_tokens += usage.candidates_token_count or 0
        self.thought_tokens += usage.thoughts_token_count or 0
  

        # Normal input tokens
        try:
            for detail in usage.prompt_tokens_details or []:
                modality = str(detail.modality).upper()
                tokens = detail.token_count or 0

                if "TEXT" in modality:
                    self.text_input_tokens += tokens

                elif "AUDIO" in modality:
                    self.audio_input_tokens += tokens

        except Exception as e:
            logger.warning(
                "Failed to process prompt token details: %s",
                e,
            )

        # Cached input tokens
        try:
            for detail in usage.cache_tokens_details or []:
                modality = str(detail.modality).upper()
                tokens = detail.token_count or 0

                if "TEXT" in modality:
                    self.cached_tokens_text += tokens

                elif "AUDIO" in modality:
                    self.cached_tokens_audio += tokens

        except Exception as e:
            logger.warning(
                "Failed to process cache token details: %s",
                e,
            )

    def _valid_pricing(self, response):
        if not isinstance(response, dict):
            return False

        try:
            AudioPricing.model_validate(response)
            return True
        except ValidationError:
            return False

    async def calculate_cost(self, model, max_retries=3):
            
        pricing = None
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                pricing = await get_model_pricing(model)
                print("Pricing",pricing)
                if self._valid_pricing(pricing):
                    break
                last_error = ValueError(f"Invalid pricing: {pricing}")
            except Exception as e:
                last_error = e
            logger.warning(f"[Observability] pricing retry {attempt}/{max_retries}: {last_error}")
            await asyncio.sleep(0.5 * attempt)
        else:
            logger.error(f"[Observability] pricing failed after retries: {last_error}")
            return
        # print("Pricing",pricing)

        # Output tokens + thought tokens
        self.output_token_cost = (
            pricing["output_price"]
            * (self.output_tokens + self.thought_tokens)
        )
        self.output_token_cost = round(self.output_token_cost, 3)

        # Text input tokens
        self.input_token_cost_text = (
            pricing["input_price_text"]
            * self.text_input_tokens
        )
        self.input_token_cost_text = round(self.input_token_cost_text, 3)

        # Audio input tokens
        self.input_token_cost_audio = (
            pricing["input_price_audio"]
            * self.audio_input_tokens
        )
        self.input_token_cost_audio = round(self.input_token_cost_audio, 3)

        # Audio cached tokens
        self.cache_token_cost_audio = (
            pricing["caching_price_audio"]
            * self.cached_tokens_audio
        )
        self.cache_token_cost_audio = round(
            self.cache_token_cost_audio, 3
        )

        # Text cached tokens
        self.cache_token_cost_text = (
            pricing["caching_price_text"]
            * self.cached_tokens_text
        )
        self.cache_token_cost_text = round(
            self.cache_token_cost_text, 3
        )


        # Total cost
        self.total_cost_usd = (
            self.input_token_cost_text
            + self.input_token_cost_audio
            + self.output_token_cost
            + self.cache_token_cost_audio
            + self.cache_token_cost_text
        )

        self.total_cost_usd = round(
            self.total_cost_usd, 3
        )
    def metrics(self):
        return {
            # Token usage
            "text_input_tokens": self.text_input_tokens,
            "audio_input_tokens": self.audio_input_tokens,
            "output_tokens": self.output_tokens,
            "thought_tokens": self.thought_tokens,
            "cached_tokens_text": self.cached_tokens_text,
            "cached_tokens_audio": self.cached_tokens_audio,
            "total_tokens": self.total_tokens,

            # Input costs
            "input_token_cost_text_usd": self.input_token_cost_text,
            "input_token_cost_audio_usd": self.input_token_cost_audio,

            # Output cost
            "output_token_cost_usd": self.output_token_cost,

            # Cache costs
            "cache_token_cost_text_usd": self.cache_token_cost_text,
            "cache_token_cost_audio_usd": self.cache_token_cost_audio,

            # Total cost
            "total_cost_usd": self.total_cost_usd,
        }