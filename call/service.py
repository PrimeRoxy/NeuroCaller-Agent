import logging
import uuid
from dotenv import load_dotenv
import os   
import boto3
import aiohttp
from dotenv import load_dotenv
from openai import OpenAI
load_dotenv()

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("S3_REGION")
S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET")

s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)


async def upload_url_to_s3(recording_url: str) -> str:
    try:
        logging.info(f"Downloading recording from URL: {recording_url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(recording_url) as response:
                if response.status != 200:
                    error_msg = f"Failed to download recording: HTTP {response.status}"
                    logging.error(error_msg)
                    return "Failed to download recording"
                file_content = await response.read()

        if not file_content:
            logging.error("Downloaded file is empty.")
            return "Downloaded file is empty"

        if not S3_BUCKET_NAME or not AWS_REGION:
            logging.error("Missing S3 configuration.")
            return "S3 configuration error"

        file_ext = ".mp3"
        file_id = f"{uuid.uuid4()}{file_ext}"
        
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=file_id,
            Body=file_content,
            ContentType="audio/mpeg"
        )
        
        s3_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{file_id}"
        return s3_url

    except Exception as e:
        error_msg = f"Upload failed on S3 bucket : {e}"
        logging.error(error_msg)
        return "Upload failed on S3 bucket"