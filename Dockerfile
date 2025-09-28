# Use official Python slim image
FROM python:3.11-slim

# Install system dependencies (ffmpeg + common build tools + audio libs)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    gcc \
    g++ \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Set workdir
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Expose Render’s port
EXPOSE 10000

# Start FastAPI app (adjust if Flask)
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "10000"]
