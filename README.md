1. Create Virtual Environment
python -m venv venv
source venv/bin/activate   
venv\Scripts\activate

2. Install Dependencies
pip install -r requirements.txt

3. Configure Environment Variables
Create a .env file in the project root:
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_service_key
TWILIO_ACCOUNT_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_auth
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
SENDGRID_API_KEY=your_sendgrid_api_key
SENDGRID_SENDER_EMAIL=alerts@safetybuddy.com
SUPERVISOR_EMAIL=pavangururaja99@gmail.com

TO RUN SERVER
uvicorn main:app --reload

APIS
POST /process_audio

Supervisor Dashboard
GET /supervisor/events → Get all SOS events with student info
POST /supervisor/acknowledge/{event_id} → Mark as acknowledged
POST /supervisor/escalate/{event_id} → Re-trigger alerts
GET /supervisor/stats → Event stats & weekly trends
