import logging
from typing import Optional, Union
from fastapi import BackgroundTasks, File, Form, HTTPException, APIRouter, UploadFile
from fastapi.responses import StreamingResponse
from services.service import generate_response, upload_file_for_gemini

router = APIRouter()

@router.post("/ask_neurocaller")
async def ask_neurocaller_endpoint(
    background_tasks: BackgroundTasks,
    query: str = Form(...),
    file: Union[UploadFile, None] = File(None),   # explicit optional
):
    """
    query: required text input
    file:  optional file upload (can be omitted entirely)
    """
    uploaded_file_part: Optional[object] = None
    try:
        # Only upload if a real file was provided
        if file and file.filename:
            uploaded_file_part = upload_file_for_gemini(
                file=file.file,
                mime_type=file.content_type
            )

        # Stream the Gemini response (works even if uploaded_file_part is None)
        return StreamingResponse(
            generate_response(query, background_tasks, uploaded_file_part),
            media_type="application/json",
        )
    except Exception as e:
        logging.error(f"Error occurred while processing multimodal request: {e}")
        raise HTTPException(status_code=500, detail=f"An internal server error occurred: {e}")
