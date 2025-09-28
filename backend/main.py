# main.py
from fastapi import FastAPI
from api import process_audio, supervisor_dashboard

app = FastAPI()

# Register routers
app.include_router(process_audio.router, prefix="/api")
app.include_router(supervisor_dashboard.router, prefix="/api", tags=["supervisor_dashboard"])