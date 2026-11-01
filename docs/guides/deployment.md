# Docker and deployment

[Documentation index](../README.md)

Run all commands from the repository root.

Docker sets `DJANGO_SETTINGS_MODULE=config.settings.production` for both startup migrations and Gunicorn. This keeps debug disabled. The WSGI entry point also defaults to production outside Docker. See [environment configuration](configuration.md) for the available modules.

The Docker image uses pinned uv 0.9.9 to run `uv sync --locked --no-dev` from `pyproject.toml` and `uv.lock`. Its own `/app/.venv` is on `PATH`, so container management commands use `python` directly and Gunicorn starts without resolving packages at runtime. Host Python and uv installations are not required for Docker.

Configure `.env` with your Gemini key, then start the container:

```sh
docker compose up --build -d
# For a new installation without an imported administrator:
docker compose exec transcription-api python manage.py createsuperuser
```

Open <http://localhost:8000>. The `transcription-data` Docker volume stores SQLite job records, uploaded recordings, completed transcript text, Django users and admin sessions, and the signing key under `/app/data`. The container entry point runs Django migrations before starting the server. Keep this volume when recreating the container; `docker compose down -v` removes its stored jobs and results. Local runs use `./data` by default; `JOB_DATA_DIR` can select another persistent directory. Job data is excluded from Git and Docker build context.

For deployment, terminate HTTPS at your reverse proxy and set `SECURE_COOKIES=true`. Add your hostname or server IP to `DJANGO_ALLOWED_HOSTS` (comma-separated, without schemes or ports). Forward the original host and scheme to Gunicorn, and set `FORWARDED_ALLOW_IPS` to your proxy's source IPs; Gunicorn only accepts scheme headers from trusted proxies. Direct local HTTP needs no proxy configuration. The app is designed for a small, multi-user deployment with **one Gunicorn worker process with eight HTTP threads**. A process lock prevents multiple queue processes from sharing a data directory. `TRANSCRIPTION_WORKERS` controls the dedicated transcription threads inside that process, independently of the web server's thread pool. It defaults to `2` (range 1–16). `MAX_PENDING_JOBS` defaults to `20`, including queued and processing jobs; a full queue returns HTTP 429. All jobs, including legacy `/transcribe` requests, share these workers.

Use the supplied Gunicorn configuration; do not enable preloading, multiple worker processes, or worker recycling while using the embedded queue. The queue starts when the WSGI serving process starts, resumes queued jobs before accepting requests, and shuts down with that process. Django management commands do not start transcription workers. `uv run --locked python manage.py runserver --noreload` is also available for local development; Gunicorn is the production entry point.

Frontend and admin use the same Django session, stored in SQLite for up to 12 hours and preserved across restarts. Signing out revokes that browser session for both interfaces. Disabling an account, deleting it, or changing its password blocks existing sessions. Sign in once after upgrading from the previous authentication system. Login is limited to five attempts per minute per client IP. `MAX_UPLOAD_MB` defaults to `500`; configure your reverse proxy's upload limit accordingly.

## Related documentation

- [Local setup](getting-started.md)
- [Accounts and administration](accounts.md)
- [Architecture](../reference/architecture.md)
