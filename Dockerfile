FROM python:3.12-slim-bookworm
# Bibliotecas do WeasyPrint (PDF das notas) + fontes
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 libfontconfig1 \
      fonts-inter fonts-dejavu-core shared-mime-info \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD gunicorn app:app --workers 1 --threads 8 --bind 0.0.0.0:${PORT:-8080} --timeout 180
