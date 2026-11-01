FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY manage.py gunicorn.conf.py ./
COPY config/ ./config/
COPY apps/ ./apps/
COPY templates/ ./templates/
COPY static/ ./static/
COPY common/ ./common/
COPY scripts/ ./scripts/
COPY docker-entrypoint.sh ./

RUN DJANGO_SECRET_KEY=static-build-only python manage.py collectstatic --noinput
RUN mkdir -p /app/outputs /app/data /tmp/opencode

EXPOSE 8000

ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]

CMD ["gunicorn", "config.wsgi:application", "--config", "gunicorn.conf.py"]
