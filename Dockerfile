FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev

COPY manage.py gunicorn.conf.py ./
COPY config/ ./config/
COPY apps/ ./apps/
COPY templates/ ./templates/
COPY static/ ./static/
COPY common/ ./common/
COPY scripts/ ./scripts/
COPY docker-entrypoint.sh ./

ENV DJANGO_SETTINGS_MODULE=config.settings.production

RUN DJANGO_SECRET_KEY=static-build-only python manage.py collectstatic --noinput
RUN mkdir -p /app/outputs /app/data /tmp/opencode

EXPOSE 8000

ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]

CMD ["gunicorn", "config.wsgi:application", "--config", "gunicorn.conf.py"]
