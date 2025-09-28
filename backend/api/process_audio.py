# api/process_audio.py
from dotenv import load_dotenv
load_dotenv()  # load .env variables

import os
import subprocess
import librosa
import numpy as np
import html
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
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")  # SMS (if you enable paid later)
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")  # sandbox
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# -----------------------------
# SendGrid Setup
# -----------------------------
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
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
# DB helpers
# -----------------------------
def get_emergency_contacts(student_id: str):
    try:
        response = supabase.table("emergency_contacts").select("*").eq("student_id", student_id).execute()
        return response.data
    except Exception:
        return []

def get_student_details(student_id: str):
    try:
        resp = supabase.table("students").select("name, email").eq("id", student_id).single().execute()
        return resp.data or {}
    except Exception:
        return {}

# -----------------------------
# Send SOS Alerts (WhatsApp + Email)
# -----------------------------
def send_sos_alerts(contacts, transcript, risk_score, latitude=None, longitude=None, student=None):
    alerts = []
    student_name = student.get("name", "Unknown") if student else "Unknown"
    student_email = student.get("email", "Unknown") if student else "Unknown"

    # Build location string
    coords_str, maps_url = "unknown", None
    if latitude is not None and longitude is not None:
        coords_str = f"{latitude:.6f}, {longitude:.6f}"
        maps_url = f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"

    # WhatsApp / Email message text
    plain_msg = (
        "🚨 EMERGENCY — Please help! 🚨\n\n"
        "I am in danger and need immediate assistance.\n\n"
    )
    if maps_url:
        plain_msg += f"Location (Google Maps): {maps_url}\n"
    plain_msg += f"Coordinates: {coords_str}\n\n"
    plain_msg += f"Student: {student_name} ({student_email})\n\n"
    plain_msg += f"Transcript: \"{transcript.strip()}\"\n"
    plain_msg += f"Risk Score: {risk_score}\n\n"
    plain_msg += "This is an automated alert from SafetyBuddy."

    # HTML version
    html_msg = (
        "<h2>🚨 EMERGENCY — Please help!</h2>"
        "<p><strong>I am in danger and need immediate assistance.</strong></p>"
    )
    if maps_url:
        html_msg += f'<p>Location: <a href="{html.escape(maps_url)}">{html.escape(coords_str)}</a></p>'
    else:
        html_msg += f"<p>Coordinates: {html.escape(coords_str)}</p>"
    html_msg += f"<p>Student: <strong>{html.escape(student_name)}</strong> ({html.escape(student_email)})</p>"
    html_msg += f"<p>Transcript: <em>{html.escape(transcript.strip())}</em></p>"
    html_msg += f"<p>Risk Score: <strong>{risk_score}</strong></p>"
    html_msg += "<p>This is an automated alert from <strong>SafetyBuddy</strong>.</p>"

    for c in contacts:
        contact_name = c.get("contact_name", "contact")
        phone = c.get("contact_phone")
        email = c.get("contact_email")

        # 1) WhatsApp
        if phone:
            try:
                to_wh = f"whatsapp:{phone}"
                twilio_client.messages.create(
                    body=plain_msg,
                    from_=TWILIO_WHATSAPP_NUMBER,
                    to=to_wh
                )
                alerts.append(f"✅ WhatsApp sent to {contact_name} ({phone})")
            except Exception as e:
                alerts.append(f"❌ Failed WhatsApp to {contact_name} ({phone}): {str(e)}")

        # 2) Email
        if email:
            try:
                message = Mail(
                    from_email=SENDGRID_SENDER,
                    to_emails=email,
                    subject="🚨 SafetyBuddy: Emergency Alert",
                    plain_text_content=plain_msg,
                )
                message.add_content(sendgrid.helpers.mail.Content("text/html", html_msg))
                resp = sg.send(message)
                alerts.append(f"✅ Email sent to {contact_name} ({email}) — status {getattr(resp, 'status_code', '')}")
            except Exception as e:
                alerts.append(f"❌ Failed Email to {contact_name} ({email}): {str(e)}")
        else:
            alerts.append(f"⚠️ No email for {contact_name}, skipping email")

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
        student_id = "00000000-0000-0000-0000-000000000001"
        lat, lon = 33.4255, -111.9400
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
        contacts = get_emergency_contacts(student_id)
        student = get_student_details(student_id)
        alerts = send_sos_alerts(
            contacts, transcript, risk_score,
            latitude=lat, longitude=lon, student=student
        ) if contacts else []

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
