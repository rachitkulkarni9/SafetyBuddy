# api/process_audio.py
from dotenv import load_dotenv
load_dotenv()  # load .env variables

import os
import subprocess
import librosa
import numpy as np
from fastapi import APIRouter, File, UploadFile
from transformers import pipeline
from supabase import create_client, Client
from twilio.rest import Client as TwilioClient
import sendgrid
from sendgrid.helpers.mail import Mail

# -----------------------------
# Supabase Setup
# -----------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# -----------------------------
# Twilio Setup
# -----------------------------
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# -----------------------------
# SendGrid Setup
# -----------------------------
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)

# Use your verified SendGrid sender email
SENDGRID_SENDER = os.getenv("SENDGRID_SENDER_EMAIL", "pbelegur@asu.edu")

router = APIRouter()

# -----------------------------
# Load AI Models
# -----------------------------
asr = pipeline(
    "automatic-speech-recognition",
    model="openai/whisper-tiny",
    chunk_length_s=10
)

emotion_model = pipeline(
    "text-classification",
    model="j-hartmann/emotion-english-distilroberta-base"
)

context_model = pipeline(
    "text-classification",
    model="bhadresh-savani/distilbert-base-uncased-emotion"
)

SOS_KEYWORDS = ["help", "stop", "leave me alone", "no"]

# -----------------------------
# Stress Detection
# -----------------------------
def analyze_stress(audio_path):
    try:
        y, sr = librosa.load(audio_path, sr=16000)
        rms = np.mean(librosa.feature.rms(y=y))
        pitches, magnitudes = librosa.piptrack(y=y, sr=sr)
        pitch = np.mean(pitches[pitches > 0]) if np.any(pitches > 0) else 0
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)

        stress_score = 0
        if rms > 0.05:
            stress_score += 0.4
        if pitch > 200:
            stress_score += 0.3
        if tempo > 120:
            stress_score += 0.3

        stress_score = min(stress_score, 1.0)
        stress_level = "HIGH" if stress_score >= 0.7 else "MEDIUM" if stress_score >= 0.4 else "LOW"
        return round(stress_score, 2), stress_level
    except Exception as e:
        return 0.0, f"error: {str(e)}"

# -----------------------------
# Text Chunking
# -----------------------------
def chunk_text(text, max_chars=300):
    words = text.split()
    chunks, current = [], []
    for w in words:
        if sum(len(x) for x in current) + len(w) > max_chars:
            chunks.append(" ".join(current))
            current = []
        current.append(w)
    if current:
        chunks.append(" ".join(current))
    return chunks

# -----------------------------
# Emergency Contacts from DB
# -----------------------------
def get_emergency_contacts(student_id: str):
    try:
        response = supabase.table("emergency_contacts").select("*").eq("student_id", student_id).execute()
        return response.data
    except Exception:
        return []

# -----------------------------
# Send SOS Alerts (SMS + Email)
# -----------------------------
def send_sos_alerts(contacts, transcript, risk_score):
    alerts = []
    for c in contacts:
        msg = f"SOS ALERT 🚨 | Transcript: {transcript} | Risk Score: {risk_score} | Contact: {c['contact_name']}"

        # --- SMS via Twilio ---
        try:
            twilio_client.messages.create(
                body=msg,
                from_=TWILIO_PHONE_NUMBER,
                to=c["contact_phone"]
            )
            alerts.append(f"✅ SMS sent to {c['contact_name']} ({c['contact_phone']})")
        except Exception as e:
            alerts.append(f"❌ Failed SMS to {c['contact_name']}: {str(e)}")

        # --- Email via SendGrid ---
        try:
            email = Mail(
                from_email=SENDGRID_SENDER,
                to_emails=c["contact_email"],
                subject="🚨 SafetyBuddy SOS Alert",
                plain_text_content=msg
            )
            sg.send(email)
            alerts.append(f"✅ Email sent to {c['contact_name']} ({c['contact_email']})")
        except Exception as e:
            alerts.append(f"❌ Failed Email to {c['contact_name']}: {str(e)}")

    return alerts

# -----------------------------
# API Endpoint
# -----------------------------
@router.post("/process_audio")
async def process_audio(file: UploadFile = File(...)):
    try:
        # Save temp file
        input_path = f"temp_{file.filename}"
        with open(input_path, "wb") as buffer:
            buffer.write(await file.read())

        fixed_path = f"fixed_{file.filename}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ac", "1", "-ar", "16000", fixed_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Step 1: Transcribe
        result = asr(fixed_path)
        transcript = result["text"].lower()

        # Step 2: Keywords
        keyword_detected = any(word in transcript for word in SOS_KEYWORDS)

        # Step 3: Emotion
        chunks = chunk_text(transcript)
        emotion_results = [emotion_model(c, truncation=True)[0] for c in chunks]
        top_emotion = max(emotion_results, key=lambda x: x["score"])
        emotion, emotion_score = top_emotion["label"], round(top_emotion["score"], 2)

        # Step 4: Context
        context_results = [context_model(c, truncation=True)[0] for c in chunks]
        top_context = max(context_results, key=lambda x: x["score"])
        context_label, context_score = top_context["label"], round(top_context["score"], 2)

        # Step 5: Stress
        stress_score, stress_level = analyze_stress(fixed_path)

        # Step 6: Risk Score
        risk_score, reasons = 0, []
        if keyword_detected:
            risk_score += 30
            reasons.append("SOS keyword detected")
        if emotion.lower() in ["fear", "anger", "sadness"]:
            risk_score += int(emotion_score * 35)
            reasons.append(f"Emotion={emotion} ({emotion_score})")
        if context_label.lower() in ["fear", "anger", "sadness"]:
            risk_score += int(context_score * 25)
            reasons.append(f"Context={context_label} ({context_score})")
        if stress_level == "HIGH":
            risk_score += 30
            reasons.append("Stress=HIGH")
        elif stress_level == "MEDIUM":
            risk_score += 15
            reasons.append("Stress=MEDIUM")

        risk_score = max(0, min(risk_score, 100))
        risk_level = "HIGH" if risk_score >= 70 else "MEDIUM" if risk_score >= 40 else "LOW"

        # Step 7: Save Event
        student_id = "00000000-0000-0000-0000-000000000001"  # TODO: frontend
        lat, lon = 33.4255, -111.9400  # TODO: from GPS
        priority = 3 if risk_level == "HIGH" else 2 if risk_level == "MEDIUM" else 1

        db_event = supabase.table("sos_events").insert({
            "student_id": student_id,
            "transcript": transcript,
            "emotion": emotion,
            "stress_level": stress_level,
            "context_label": context_label,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "latitude": lat,
            "longitude": lon,
            "priority": priority
        }).execute()

        # Step 8: Trigger Alerts
        alerts = []
        if risk_level in ["HIGH", "MEDIUM"]:
            contacts = get_emergency_contacts(student_id)
            if contacts:
                alerts = send_sos_alerts(contacts, transcript, risk_score)

        # Cleanup
        os.remove(input_path)
        os.remove(fixed_path)

        return {
            "transcript": transcript,
            "keyword_detected": keyword_detected,
            "emotion": emotion,
            "emotion_score": emotion_score,
            "stress_score": stress_score,
            "stress_level": stress_level,
            "context_label": context_label,
            "context_score": context_score,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "reasoning": "; ".join(reasons),
            "db_event": db_event.data,
            "alerts_triggered": alerts
        }

    except Exception as e:
        return {"error": str(e)}
