from fastapi import FastAPI
from router import rag_router,realtime_router,config_org
from call import plivo,call_stream
from fastapi.middleware.cors import CORSMiddleware
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(realtime_router.router, prefix="/api", tags=["Realtime Sales Agent"])
app.include_router(rag_router.router, prefix="/api", tags=["Knowledge Injection"])
app.include_router(plivo.router, tags=["Calling"])
app.include_router(call_stream.router, tags=["streaming Call"])
app.include_router(config_org.router, tags=["ORG DATA"])
