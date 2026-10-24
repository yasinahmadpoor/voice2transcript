FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY transcription_diarize.py transcription_api.py ./

RUN mkdir -p /app/outputs /tmp/opencode

EXPOSE 8000

CMD ["uvicorn", "transcription_api:app", "--host", "0.0.0.0", "--port", "8000"]
