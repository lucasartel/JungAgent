FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy Python modules and package directories.
COPY *.py .
COPY core/ ./core/
COPY work/ ./work/
COPY engines/ ./engines/

# Copy dashboard, helper scripts, and migrations.
COPY admin_web/ ./admin_web/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY tests/verify_phase3.py ./tests/verify_phase3.py
COPY tests/verify_phase4.py ./tests/verify_phase4.py

# Create runtime directories.
RUN mkdir -p /data /app/chroma_db /app/logs

# Verify critical migration files are present in the image.
RUN ls -la /app/migrations/ && \
    test -f /app/migrations/006_agent_identity_nuclear.sql && \
    echo "Migration file found in build" || \
    (echo "Migration file NOT found in build" && exit 1)

EXPOSE 8000

CMD ["python", "main.py"]
