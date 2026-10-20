
import os
import asyncio
import logging
import phonenumbers
import math
import json
from phonenumbers import region_code_for_number

# # Initialize MongoDB (Separate connection to avoid circular imports)
# MONGO_URI = os.getenv("MONGO_URI")
# try:
#     mongo_client = MongoClient(MONGO_URI)
#     db = mongo_client.get_database()
#     calls_collection = db["calls"]
# except Exception as e:
#     logger.error(f"Failed to connect to MongoDB: {e}")

logger = logging.getLogger("Costcalculation")
logger.setLevel(logging.INFO)

GPT_REAL_TIME_MODEL = "gpt-realtime-mini"
GEMINI_FLASH_MODEL = "gemini-flash-latest"
openai_analysis_model = os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-4.1-mini")


def get_country_from_number(phone_number: str):
    phone_number = str(phone_number).strip()

    if not phone_number.startswith("+"):
        phone_number = "+" + phone_number

    number = phonenumbers.parse(phone_number, None)

    return region_code_for_number(number)


class UsageCostTracker:
    def __init__(self,call_uuid,redis_client,call_collection,analysis_metrics=None):

        self.call_uuid = call_uuid
        self.call_collection = call_collection
        self.call_doc = call_collection.find_one({"call_uuid": call_uuid})
        self.provider = self.call_doc.get("provider")
        self.analysis_metrics =analysis_metrics
        self.audio_obs_metrics= None
        self.analysis_metrics=analysis_metrics or None
        self.sentiment_metrics = None
        self.transcription_obs_metrics = None
        self.elevan_labs_price = None
        self.duration_sec = self.call_doc.get("duration", 0) if self.call_doc else 0
        self.provider_cost = None
        self.queue = redis_client
        self.total_price = 0

    async def load_metrics(self):

        if self.provider == "voicelink":
            try:
                for _ in range(10):
                    self.sentiment_metrics = self.queue.get(f"sentiment_analysis_metrics:{self.call_uuid}")
                    self.audio_obs_metrics = self.queue.get(f"call_audio_obs:{self.call_uuid}")
                    # self.duration_sec = self.queue.get(f"call_duration:{self.call_uuid}")   
                    self.transcription_obs_metrics = self.queue.get(f"call_transcription_obs:{self.call_uuid}")
                    self.elevan_labs_price = self.queue.get(f"elevan_labs_price:{self.call_uuid}")
                    if self.audio_obs_metrics is not None and self.duration_sec is not None and self.transcription_obs_metrics is not None and self.elevan_labs_price is not None and self.sentiment_metrics is not None:
                        break

                    await asyncio.sleep(1)
                    # logger.info(f"[Analysis] Waiting for audio and transcription metrics for {self.call_uuid}")

            except Exception as e:
                logger.error(f"[Analysis] Failed to get voicelink observability metrics: {e}")

        elif self.provider == "plivo":
            try:
                for _ in range(10):
                    self.sentiment_metrics = self.queue.get(f"sentiment_analysis_metrics:{self.call_uuid}")
                    self.audio_obs_metrics = self.queue.get(f"call_audio_obs:{self.call_uuid}")
                    # self.duration_sec = self.queue.get(f"call_duration:{self.call_uuid}")
                    
                    if self.audio_obs_metrics is not None and self.duration_sec is not None and self.sentiment_metrics is not None:
                        break

                    await asyncio.sleep(1)
                    # logger.info(f"[Analysis] Waiting for audio and transcription metrics for {self.call_uuid}")

            except Exception as e:
                logger.error(f"[Analysis] Failed to get voicelink observability metrics: {e}")
        else:
            try:
                for _ in range(10):
                    self.sentiment_metrics = self.queue.get(f"sentiment_analysis_metrics:{self.call_uuid}")
                    self.audio_obs_metrics = self.queue.get(f"call_audio_obs:{self.call_uuid}")
                    # self.duration_sec = self.call_doc.get("duration", 0) if self.call_doc else 0
                    self.transcription_obs_metrics = self.queue.get(f"call_transcription_obs:{self.call_uuid}")

                    # print("audio_obs_metrics:",self.audio_obs_metrics)
                    # print("duration_sec:",self.duration_sec)
                    # print("transcription_obs_metrics:",self.transcription_obs_metrics)
                    print("sentiment_metrics:",self.sentiment_metrics)
                    if self.audio_obs_metrics is not None and self.duration_sec is not None and self.transcription_obs_metrics is not None and self.sentiment_metrics is not None:
                        break

                    await asyncio.sleep(1)
                    # logger.info(f"[Analysis] Waiting for audio and transcription metrics for {self.call_uuid}")

            except Exception as e:
                logger.error(f"[Analysis] Failed to get voicelink observability metrics: {e}")

        if self.audio_obs_metrics is not None:
            if isinstance(self.audio_obs_metrics, bytes):
                self.audio_obs_metrics = self.audio_obs_metrics.decode("utf-8")

            self.audio_obs_metrics = json.loads(self.audio_obs_metrics)

        if self.transcription_obs_metrics is not None:
            if isinstance(self.transcription_obs_metrics, bytes):
                self.transcription_obs_metrics = self.transcription_obs_metrics.decode("utf-8")

            self.transcription_obs_metrics = json.loads(self.transcription_obs_metrics)

        if self.duration_sec is not None:
            if isinstance(self.duration_sec, bytes):
                self.duration_sec = self.duration_sec.decode("utf-8")
            try:
                self.duration_sec = float(self.duration_sec)
            except (ValueError, TypeError):
                self.duration_sec = float(self.call_doc.get("duration", 0) if self.call_doc else 0)
        else:
            self.duration_sec = float(self.call_doc.get("duration", 0) if self.call_doc else 0)

        if self.sentiment_metrics is not None:
            if isinstance(self.elevan_labs_price,bytes):
                self.sentiment_metrics = self.sentiment_metrics.decode("utf-8")

            self.sentiment_metrics = json.loads(self.sentiment_metrics)

        if self.elevan_labs_price is not None:
            if isinstance(self.elevan_labs_price,bytes):
                self.elevan_labs_price = self.elevan_labs_price.decode("utf-8")

            self.elevan_labs_price = json.loads(self.elevan_labs_price)

    async def calculate_provider_cost(self):

        if self.provider == "voicelink":
            self.provider_cost = None
            return 
        # provider = call_doc.get("provider")
        to_number = self.call_doc.get("to_number")
        # logger.info(f"[Provider Cost]: Provider is {provider}")
        # logger.info(f"[Provider Cost]: User is {to_number}")
        # logger.info(
        #     "[Provider Cost]: User is %r, type=%s",
        #     to_number,
        #     type(to_number).__name__
        # )
        duration_sec = float(self.duration_sec or (self.call_doc.get("duration", 0) if self.call_doc else 0) or 0)
        duration_min = math.ceil(duration_sec / 60)

        country = "IN"
        if to_number:
            try:
                country = await asyncio.to_thread(
                    get_country_from_number,
                    to_number
                )
            except Exception as e:
                logger.warning(f"[Provider Cost] Could not determine country from number {to_number}: {e}")
                country = "IN"

        pricing = {
            ("twilio", "US"): 0.0140,
            ("twilio", "AE"): 0.2995,
            ("twilio", "IN"): 0.0496,
            ("plivo", "US"): 0.0115,
            ("frejun", "AE"): 0.1220,
            ("plivo", "IN"): 0.00397,
            ("tata", "IN"): 0.0,
        }

        provider_key = (self.provider or "").lower()
        key = (provider_key, country)

        # Fallback to 0.0 if not provided
        cost_per_minute = pricing.get(key, 0.0)
        total_cost = round(duration_min * cost_per_minute, 3)

        self.provider_cost = {
            "country": country,
            "duration_seconds": duration_sec,
            "duration_minutes": duration_min,
            "cost_per_minute_usd": cost_per_minute,
            "total_cost_usd": total_cost,
        }
    

    async def put_metrics_to_mongodb(self):

        update_data = {}

        self.total_price = 0
        if self.analysis_metrics is not None:
            self.total_price += self.analysis_metrics.get("total_cost_usd", 0)
            update_data["analysis_token_metrics.analysis_tokens"] = {
                "model": openai_analysis_model,
                "observability": self.analysis_metrics
            }

        if self.audio_obs_metrics is not None:
            self.total_price += self.audio_obs_metrics.get("total_cost_usd", 0)
            update_data["analysis_token_metrics.audio_tokens"] = {
                "model": GPT_REAL_TIME_MODEL,
                "observability": self.audio_obs_metrics
            }
        if self.transcription_obs_metrics is not None:
            self.total_price += self.transcription_obs_metrics.get("total_cost_usd", 0)
            update_data["analysis_token_metrics.transcription_tokens"] = {
                "model": GEMINI_FLASH_MODEL,
                "observability": self.transcription_obs_metrics
            }
        if self.provider_cost is not None:
            self.total_price += self.provider_cost.get("total_cost_usd", 0)
            update_data["analysis_token_metrics.provider_details"] = {
                "provider":self.provider,
                "price":self.provider_cost
            }
        
        if self.elevan_labs_price is not None:
            self.total_price += self.elevan_labs_price
            update_data["analysis_token_metrics.elevan_labs_price"] = {
                "price":self.elevan_labs_price
            }
        if self.sentiment_metrics is not None:
            self.total_price+= self.sentiment_metrics.get("total_cost_usd", 0)
            update_data["analysis_token_metrics.sentiment_analysis_metrics"] = {
                "sentiments_metrics":self.sentiment_metrics
            }
        if self.total_price is not None:
            update_data["analysis_token_metrics.total_price"] = {
                "price":self.total_price
            }
        self.total_price = round(self.total_price, 3)
        
        if update_data:
            self.call_collection.update_one(
                {"call_uuid": self.call_uuid},
                {"$set": update_data}
            )

        
        
        logger.info(f"[Analysis] Total price of the metrics: {self.total_price}")
 




        