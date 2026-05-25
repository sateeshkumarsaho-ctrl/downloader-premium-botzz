FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates build-essential gcc python3-dev \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN useradd --create-home --shell /usr/sbin/nologin botuser \
    && mkdir -p /app/downloads /app/sessions \
    && chown -R botuser:botuser /app

USER botuser

CMD ["python", "-m", "app.main"]
