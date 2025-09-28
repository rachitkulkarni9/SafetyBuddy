# api/supervisor_dashboard.py
from fastapi import APIRouter
from supabase import create_client, Client
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

router = APIRouter()

@router.get("/supervisor/events")
async def get_all_events():
    try:
        response = supabase.table("sos_events") \
            .select("id, student_id, transcript, emotion, stress_level, context_label, risk_score, risk_level, latitude, longitude, created_at, students(name, email)") \
            .order("created_at", desc=True) \
            .execute()
        return response.data
    except Exception as e:
        return {"error": str(e)}
