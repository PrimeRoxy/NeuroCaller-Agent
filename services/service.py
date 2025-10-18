import logging
import os
from fastapi import BackgroundTasks, APIRouter
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types
from call.plivo import outbound_call
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable not set.")

router = APIRouter()

client = genai.Client(api_key=GOOGLE_API_KEY)
# Gemini model ID supporting multimodal input
MODEL_NAME = "gemini-2.5-flash" 

# --- Helper Function for File Upload ---

def upload_file_for_gemini(file, mime_type: str) -> types.File:
    """Uploads a local file to the Gemini File API."""
    try:
        logging.info(f"Uploading file: {file} with mime type: {mime_type}")
        # The client.files.upload method handles the actual upload
        uploaded_file = client.files.upload(
            file=file,
            config=types.UploadFileConfig(mime_type=mime_type)
        )
        logging.info(f"File uploaded successfully: {uploaded_file.name}")
        return uploaded_file
    except Exception as e:
        logging.error(f"Error uploading file {file}: {e}")
        # In a real app, you'd want robust error handling, perhaps raising an exception
        return None

outbound_call_declaration = {
    "name": "outbound_call",
    "description": "Initiate outbound phone calls to multiple numbers.",
    "parameters": {
        "type": "object",
        "properties": {
            "phone_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "All phone numbers in +<countrycode><number> format"
            },
            "organisation_id": {
                "type": "string",
                "description": "Organisation identifier parsed from the uploaded document or user message (optional)."
            },
            "user_id": {
                "type": "string",
                "description": "User identifier parsed from the uploaded document or user message (optional)."
            }
        },
        "required": ["phone_numbers"]
        
    }
}


tools = types.Tool(function_declarations=[outbound_call_declaration])

def generate_response(query: str, background_tasks: BackgroundTasks, uploaded_file: types.File = None):
    """
    Generator function to stream a grounded response from Gemini 2.5 Flash, 
    supporting multimodal input (file/audio).
    """
    # --- PERFECTED SYSTEM INSTRUCTION INTEGRATION ---
    system_instruction = (
        "You are an expert **sales calling agent assistant**.\n"
        "\n"
        "Your primary job is to answer the user’s questions clearly and persuasively, "
        "just like a professional sales representative.\n"
        "\n"
        "If the user explicitly requests that you **call**, **reach**, or otherwise "
        "phone someone AND a PDF/CSV file is provided that contains phone numbers:\n"
        "   1. Extract all phone numbers in +<countrycode><number> format.\n"
        "   2. Call the `outbound_call` tool, passing these numbers as an array "
        "      under the key `phone_numbers`.\n"
        "   3. If no numbers are found, politely inform the user that no valid "
        "      phone numbers are available.\n"
        "\n"
        "If the file is uploaded for any other purpose (for example, to answer a "
        "different kind of question), simply use it as context for your answer and "
        "**do not call the tool**.\n"
        "\n"
        "Do not use any external knowledge base. Rely only on the user question "
        "and any uploaded file. Always give concise, professional sales-oriented "
        "responses."
    )

    # --- CONTENT CONSTRUCTION ---
    
    parts = []

    # 2. Add the uploaded file/audio (FILE/AUDIO INPUT)
    if uploaded_file:
        # The uploaded_file object is directly added as a multimodal Part
        parts.append(uploaded_file) 
        
    # 3. Add the user's query
    parts.append(f"User Query: {query}")
    
    contents = parts

    # --- API CALL AND STREAMING ---
    logging.info("Calling Gemini 2.5 Flash with multimodal/RAG content...")
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config=types.GenerateContentConfig(
            tools=[tools],
            system_instruction=system_instruction
        )
    )
    if (response.candidates and 
        response.candidates[0].content.parts and 
        response.candidates[0].content.parts[0].function_call):
        
        function_call = response.candidates[0].content.parts[0].function_call
        
        print(f"Function to call: {function_call.name}")
        print(f"Arguments: {function_call.args}")

        if function_call.name == "outbound_call":
            phone_numbers = function_call.args.get("phone_numbers", [])
            org_id = function_call.args.get("organisation_id")  # optional
            user_id = function_call.args.get("user_id")         # optional
        if phone_numbers:
            background_tasks.add_task(outbound_call, ",".join(phone_numbers),org_id, user_id)
            yield json.dumps({
                "message": f"Contact extraction complete! Successfully processed **{len(phone_numbers)}** phone numbers."
                           f"All {len(phone_numbers)} contacts have been queued for automated sales outreach.",
            })
            return
        
        # If a function call was suggested but was not the expected format/tool
        yield "The model suggested a tool call, but the parameters or function name were unexpected. No calls were initiated."

    else:
        if response.text:
            logging.info(f"Gemini 2.5 Flash response: {response.text}")
            yield json.dumps({"message": response.text})
        else:
            yield json.dumps({"message": "The model did not return a function call or a text response."})

