import asyncio
import redis
from sentiment.sentiment import _get_sentiments


async def main():
    print("This script is running directly!")

    # REDIS_URL = "redis://default:YOUR_PASSWORD@redis-19078.crce206.ap-south-1-1.ec2.cloud.redislabs.com:19078"

    # r = redis.Redis.from_url(REDIS_URL)

    audio_url = "https://media.plivo.com/v1/Account/MANDEXMTRINWE1ZTY5MM/Recording/3c5ea9e0-43f0-4028-b54d-3888811611a7.mp3"

    response = await _get_sentiments(audio_url)
    print(response)

    # r.ping()

    # pubsub = r.pubsub()
    # pubsub.subscribe("test_channel")

    # print("PubSub connected")
    # print("Redis connected")


if __name__ == "__main__":
    asyncio.run(main())