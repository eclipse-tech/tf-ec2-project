# Dockerfile — Python Flask app
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY app.py .

# Create log directory (CloudWatch Agent reads from here via bind mount)
RUN mkdir -p /var/log/app

EXPOSE 8080

# Use gunicorn for production — 2 workers, logs to stdout
CMD ["gunicorn", "--workers=2", "--bind=0.0.0.0:8080", \
     "--access-logfile=-", "--error-logfile=-", \
     "--log-level=info", "app:app", \
     "--preload"]
