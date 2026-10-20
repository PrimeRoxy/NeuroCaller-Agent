import json, os, asyncio
from google import genai
from google.genai import types
from dotenv import load_dotenv
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
import logging

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
    "gemini-2.5-flash": {
        "input_price": 0.30 / 1_000_000,
        "output_price": 2.50 / 1_000_000,
        "caching_price": 0.03 / 1_000_000,
    },
    "gemini-2.5-flash-lite": {
        "input_price": 0.05 / 1_000_000,
        "output_price": 0.25 / 1_000_000,
        "caching_price": 0.01 / 1_000_000,
    },
    "gemini-3.5-flash-lite": {
        "input_price": 0.30 / 1_000_000,
        "output_price": 2.50 / 1_000_000,
        "caching_price": 0.03 / 1_000_000,
    },
    "gemini-3.5-flash": {
        "input_price": 1.50 / 1_000_000,
        "output_price": 9.00 / 1_000_000,
        "caching_price": 0.15 / 1_000_000,
    },
    "gemini-flash-latest": {
        "input_price": 0.30 / 1_000_000,
        "output_price": 2.50 / 1_000_000,
        "caching_price": 0.03 / 1_000_000,
    },
    "gemini-flash-lite-latest": {
        "input_price": 0.10 / 1_000_000,
        "output_price": 0.40 / 1_000_000,
        "caching_price": 0.01 / 1_000_000,
    },
    "gpt-4.1-mini": {
        "input_price": 0.15 / 1_000_000,
        "output_price": 0.60 / 1_000_000,
        "caching_price": 0.075 / 1_000_000,
    },
    "gpt-4o-mini": {
        "input_price": 0.15 / 1_000_000,
        "output_price": 0.60 / 1_000_000,
        "caching_price": 0.075 / 1_000_000,
    },
}

OPENAI_REALTIME_MINI_PRICING ={
    "gpt-realtime-mini":{
        "input_text_price": 0.60 / 1_000_000,
        "cached_text_price": 0.06 / 1_000_000,

        "input_audio_price": 10.00 / 1_000_000,
        "cached_audio_price": 0.30 / 1_000_000,

        "output_text_price": 2.40 / 1_000_000,
        "output_audio_price": 20.00 / 1_000_000,
    }
}
# Gemini client used only by search_model_pricing() when a model is not in MODEL_PRICING.
# Requires GEMINI_API_KEY in the environment.
API = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=API)

async def search_model_pricing(model: str):
    """
    Fallback: look up official API pricing for an unknown model
    using Gemini + Google Search.

    Returns pricing in USD per token.
    """

    prompt = f"""
    Search the official pricing documentation for this model:

    {model}

    Only use the model provider's official pricing page.

    If this is a standard text model, return JSON in this format:

    {{
        "model": "{model}",
        "input_price": 0.0,
        "output_price": 0.0,
        "caching_price": 0.0
    }}

    If this is a realtime/audio model, return JSON in this format:

    {{
        "model": "{model}",
        "input_text_price": 0.0,
        "input_audio_price": 0.0,
        "input_image_price": 0.0,
        "cached_text_price": 0.0,
        "cached_audio_price": 0.0,
        "cached_image_price": 0.0,
        "output_text_price": 0.0,
        "output_audio_price": 0.0
    }}

    Rules:
    - All prices must be USD per 1 million tokens.
    - For standard models:
        - input_price = input token price
        - output_price = output token price
        - caching_price = cached input token price

    - For realtime/audio models:
        - input_text_price = text input token price
        - input_audio_price = audio input token price
        - input_image_price = image input token price
        - cached_text_price = cached text input token price
        - cached_audio_price = cached audio input token price
        - cached_image_price = cached image input token price
        - output_text_price = text output token price
        - output_audio_price = audio output token price

    - If a pricing category does not exist, return 0.
    - Do not use third-party pricing pages.
    - Do not return explanations.
    - Return ONLY valid JSON.
    """

    response = await asyncio.to_thread(
        client.models.generate_content,
        model="gemini-3.1-flash-lite-preview",
        contents=prompt,
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

    # Standard model
    if "input_price" in pricing:
        return {
            "input_price": pricing["input_price"] / 1_000_000,
            "output_price": pricing["output_price"] / 1_000_000,
            "caching_price": pricing["caching_price"] / 1_000_000,
        }

    # Realtime / audio model
    return {
        "input_text_price": pricing["input_text_price"] / 1_000_000,
        "input_audio_price": pricing["input_audio_price"] / 1_000_000,
        "input_image_price": pricing["input_image_price"] / 1_000_000,

        "cached_text_price": pricing["cached_text_price"] / 1_000_000,
        "cached_audio_price": pricing["cached_audio_price"] / 1_000_000,
        "cached_image_price": pricing["cached_image_price"] / 1_000_000,

        "output_text_price": pricing["output_text_price"] / 1_000_000,
        "output_audio_price": pricing["output_audio_price"] / 1_000_000,
    }

async def get_model_pricing_gpt(model):

    if model in OPENAI_REALTIME_MINI_PRICING:
        return OPENAI_REALTIME_MINI_PRICING[model]

    pricing = await search_model_pricing(model)

    if pricing is None:
        raise ValueError(f"Pricing not found for model: {model}")

    return pricing

async def get_model_pricing(model):
    """
    Resolve pricing for a model name.
    1. Return from MODEL_PRICING if the model is known locally (fast path).
    2. Fallback to model name search or default pricing.
    """
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]

    # Try searching online
    try:
        pricing = await search_model_pricing(model)
        if pricing:
            return pricing
    except Exception as e:
        logger.warning(f"Failed to search pricing online for model '{model}': {e}")

    # Fallback default pricing if not found
    return {
        "input_price": 1.50 / 1_000_000,
        "output_price": 7.50 / 1_000_000,
        "caching_price": 0.15 / 1_000_000,
    }

class Observability:
    def __init__(self, model="gemini-2.5-flash"):
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0
        self.thought_tokens = 0
        self.cached_tokens = 0
        self.total_tokens = 0

        self.input_token_cost = 0
        self.output_token_cost = 0
        self.cache_token_cost = 0
        self.total_cost_usd = 0

    def record(self, usage):
        if usage is None:
            return

        def _val(*attr_names):
            for attr_name in attr_names:
                if isinstance(usage, dict):
                    val = usage.get(attr_name, 0)
                else:
                    val = getattr(usage, attr_name, 0)
                if val:
                    return val
            return 0

        self.input_tokens += _val("prompt_token_count", "prompt_tokens")
        self.output_tokens += _val("candidates_token_count", "completion_tokens")
        self.thought_tokens += _val("thoughts_token_count")
        
        cached = _val("cached_content_token_count")
        if not cached and hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details:
            cached = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
        self.cached_tokens += cached

        self.total_tokens += _val("total_token_count", "total_tokens")

    async def after_model_callback(
        self,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ):
        usage = llm_response.usage_metadata

        if usage:
            self.record(usage)

            def _val(attr_name):
                if isinstance(usage, dict):
                    return usage.get(attr_name, 0) or 0
                return getattr(usage, attr_name, 0) or 0

            prompt_cnt = _val("prompt_token_count")
            cand_cnt = _val("candidates_token_count")
            thought_cnt = _val("thoughts_token_count")
            cached_cnt = _val("cached_content_token_count")
            tot_cnt = _val("total_token_count")

            logger.info(f"[tokens] input={prompt_cnt:,}")
            logger.info(f"[tokens] output={cand_cnt:,}")
            logger.info(f"[tokens] thoughts={thought_cnt:,}")
            logger.info(f"[tokens] cached={cached_cnt:,}")
            logger.info(f"[tokens] total={tot_cnt:,}")

            try:
                await self.calculate_cost(self.model)

                logger.info(f"Input price: {self.input_token_cost}")
                logger.info(f"Output price: {self.output_token_cost}")
                logger.info(f"Cache price: {self.cache_token_cost}")
                logger.info(f"Total price: {self.total_cost_usd}")
            except Exception as e:
                logger.warning(f"Cost calculation error in after_model_callback: {e}")

        return None


    async def calculate_cost(self, model):
        try:
            pricing = await get_model_pricing(model)
            print("pricing:",pricing)
        except Exception as e:
            logger.warning(f"Could not resolve model pricing: {e}")
            pricing = {}

        input_price = pricing.get("input_price") or pricing.get("input_text_price") or 0.0
        output_price = pricing.get("output_price") or pricing.get("output_text_price") or 0.0
        cache_price = pricing.get("caching_price") or pricing.get("cached_text_price") or 0.0

        self.input_token_cost = round(input_price * self.input_tokens, 3)
        print("input_token_cost:",self.input_token_cost)
        self.output_token_cost = round(
            output_price * (self.output_tokens + self.thought_tokens), 3
        )
        print("output_token_cost:",self.output_token_cost)
        self.cache_token_cost = round(cache_price * self.cached_tokens, 3)
        self.total_cost_usd = (
            self.input_token_cost
            + self.output_token_cost
            + self.cache_token_cost
        )

    def metrics(self):
        res = {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "thought_tokens": self.thought_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
        }
        return res




class RealtimeAudioObservability:
    """
    Tracks OpenAI Realtime audio usage and calculates cost.
    """

    def __init__(self):
        # Overall usage
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0

        # Input tokens
        self.input_text_tokens = 0
        self.input_audio_tokens = 0
        self.input_image_tokens = 0

        # Output tokens
        self.output_text_tokens = 0
        self.output_audio_tokens = 0

        # Cached input tokens
        self.cached_tokens = 0
        self.cached_text_tokens = 0
        self.cached_audio_tokens = 0
        self.cached_image_tokens = 0

        # Costs
        self.input_cost_usd = 0
        self.output_cost_usd = 0
        self.cache_cost_usd = 0
        self.total_cost_usd = 0

    def record(self, usage):
        """
        Record usage from an OpenAI Realtime `response.done` event.
        """

        self.input_tokens += usage.get("input_tokens", 0) or 0
        self.output_tokens += usage.get("output_tokens", 0) or 0
        self.total_tokens += usage.get("total_tokens", 0) or 0

        # -------------------------
        # Input tokens
        # -------------------------

        input_details = usage.get("input_token_details") or {}

        self.input_text_tokens += (
            input_details.get("text_tokens", 0) or 0
        )

        self.input_audio_tokens += (
            input_details.get("audio_tokens", 0) or 0
        )

        self.input_image_tokens += (
            input_details.get("image_tokens", 0) or 0
        )

        # -------------------------
        # Cached tokens
        # -------------------------

        self.cached_tokens += (
            input_details.get("cached_tokens", 0) or 0
        )

        cached_details = (
            input_details.get("cached_tokens_details") or {}
        )

        self.cached_text_tokens += (
            cached_details.get("text_tokens", 0) or 0
        )

        self.cached_audio_tokens += (
            cached_details.get("audio_tokens", 0) or 0
        )

        self.cached_image_tokens += (
            cached_details.get("image_tokens", 0) or 0
        )

        # -------------------------
        # Output tokens
        # -------------------------

        output_details = usage.get("output_token_details") or {}

        self.output_text_tokens += (
            output_details.get("text_tokens", 0) or 0
        )

        self.output_audio_tokens += (
            output_details.get("audio_tokens", 0) or 0
        )

    async def calculate_cost(self, model):
        """
        pricing should contain separate prices for:
        input text/audio,
        cached text/audio,
        output text/audio.

        Prices should be USD per token.
        """
        pricing = await get_model_pricing_gpt(model)

        uncached_text = max(0, self.input_text_tokens - self.cached_text_tokens)
        uncached_audio = max(0, self.input_audio_tokens - self.cached_audio_tokens)

        self.input_cost_usd = (
            uncached_text * pricing["input_text_price"]
            + uncached_audio * pricing["input_audio_price"]
        )
        self.input_cost_usd =round(self.input_cost_usd,3)
        self.output_cost_usd = (
            self.output_text_tokens * pricing["output_text_price"]
            + self.output_audio_tokens * pricing["output_audio_price"]
        )

        self.output_cost_usd = round(self.output_cost_usd,3)
        
        self.cache_cost_usd = (
            self.cached_text_tokens * pricing["cached_text_price"]
            + self.cached_audio_tokens * pricing["cached_audio_price"]
        )

        self.cache_cost_usd =round(self.cache_cost_usd,3)

        self.total_cost_usd = (
            self.input_cost_usd
            + self.output_cost_usd
            + self.cache_cost_usd
        )

    def metrics(self):
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,

            "input_text_tokens": self.input_text_tokens,
            "input_audio_tokens": self.input_audio_tokens,
            "input_image_tokens": self.input_image_tokens,

            "output_text_tokens": self.output_text_tokens,
            "output_audio_tokens": self.output_audio_tokens,

            "cached_tokens": self.cached_tokens,
            "cached_text_tokens": self.cached_text_tokens,
            "cached_audio_tokens": self.cached_audio_tokens,
            "cached_image_tokens": self.cached_image_tokens,

            "input_cost_usd": self.input_cost_usd,
            "output_cost_usd": self.output_cost_usd,
            "cache_cost_usd": self.cache_cost_usd,
            "total_cost_usd": self.total_cost_usd,
        }