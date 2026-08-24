FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first so pip install is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code: Flask backend, Jinja templates, and static assets (manifest,
# service worker, PWA icons). Missing static/ would 404 the PWA install.
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# Bind mount point for the SQLite DB and any writable state.
RUN mkdir -p /data

# Matches the default port in config.json.example.
EXPOSE 5100

CMD ["python", "app.py"]