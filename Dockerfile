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

# Copiar todos os arquivos Python
COPY *.py .

# Copiar o diretório admin_web com templates e static
COPY admin_web/ ./admin_web/
COPY scripts/ ./scripts/

# CRÍTICO: Copiar pasta migrations com os SQL files
COPY migrations/ ./migrations/

# Criar diretórios necessários
RUN mkdir -p /data /app/chroma_db /app/logs

# Verificar se migration file existe (debug)
RUN ls -la /app/migrations/ && \
    test -f /app/migrations/006_agent_identity_nuclear.sql && \
    echo "✅ Migration file encontrado no build" || \
    (echo "❌ Migration file NÃO encontrado no build" && exit 1)

EXPOSE 8000

# Rodar o main.py que gerencia bot + FastAPI
CMD ["python", "main.py"]
