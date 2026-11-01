# Voice to Transcript

A Django app that transcribes audio or video with Gemini and adds speaker labels. Recordings run as persistent background jobs with **two active transcriptions** by default. The interface includes login, upload and preview, language and model settings, an editable prompt, recent job history, status updates, retries, and editable transcripts with copying and text downloads. Persian is the default language hint; leave it blank for auto-detection. Settings start collapsed and load defaults from the API.

The interface starts in dark mode. Use the theme button in the header to switch between dark and light; your choice is saved in the browser and applies to both the login page and workspace.

Select or drop multiple audio/video files to create a batch. Review sizes, preview recordings, and remove individual files before submitting. Shared transcription settings are captured once for the batch. Files upload sequentially; each accepted file becomes a separate background job, while two transcriptions can run at once. The per-file size limit still applies.

Each file shows its upload status. Invalid files are skipped, and failed files can be retried without resubmitting successful ones. If the queue fills or the connection drops, remaining uploads stop and can be retried later. Retry keys prevent duplicate jobs when a server response is lost. Clearing the upload list does not cancel jobs already submitted. Keep the page open until uploads finish; file selections and unsubmitted uploads are not preserved after refreshing or closing it.

## Run locally

Requires Python 3.10+ and `ffmpeg` / `ffprobe` on your PATH.

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` from `.env.example` if you don't have one, and set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`). Keep your existing `.env` if it already contains a key. Configure your account:

```sh
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --noinput
gunicorn config.wsgi:application --config gunicorn.conf.py --bind 127.0.0.1:8000
```

Open <http://localhost:8000> and sign in using the Django account you created. `createsuperuser` prompts for a username and password; no default password is supplied. Additional people can be added through Django admin. Existing installations with `APP_USERNAME` and `APP_PASSWORD_HASH` are imported once during the user-ownership migration, preserving the original login. After migration, passwords and account status are managed in Django admin or with `python manage.py changepassword USERNAME`; `.env` credentials no longer override database accounts. `python -m scripts.bootstrap_login` remains an initial legacy-bootstrap helper for a fresh database.

## Docker

Configure `.env` with your Gemini key, then start the container:

```sh
docker compose up --build -d
# For a new installation without an imported administrator:
docker compose exec transcription-api python manage.py createsuperuser
```

Open <http://localhost:8000>. The `transcription-data` Docker volume stores SQLite job records, uploaded recordings, completed transcript text, Django users and admin sessions, and the signing key under `/app/data`. The container entry point runs Django migrations before starting the server. Keep this volume when recreating the container; `docker compose down -v` removes its stored jobs and results. Local runs use `./data` by default; `JOB_DATA_DIR` can select another persistent directory. Job data is excluded from Git and Docker build context.

For deployment, terminate HTTPS at your reverse proxy and set `SECURE_COOKIES=true`. Add your hostname or server IP to `DJANGO_ALLOWED_HOSTS` (comma-separated, without schemes or ports). Forward the original host and scheme to Gunicorn, and set `FORWARDED_ALLOW_IPS` to your proxy's source IPs; Gunicorn only accepts scheme headers from trusted proxies. Direct local HTTP needs no proxy configuration. The app is designed for a small, multi-user deployment with **one Gunicorn worker process with eight HTTP threads**. A process lock prevents multiple queue processes from sharing a data directory. `TRANSCRIPTION_WORKERS` controls the dedicated transcription threads inside that process, independently of the web server's thread pool. It defaults to `2` (range 1–16). `MAX_PENDING_JOBS` defaults to `20`, including queued and processing jobs; a full queue returns HTTP 429. All jobs, including legacy `/transcribe` requests, share these workers.

Use the supplied Gunicorn configuration; do not enable preloading, multiple worker processes, or worker recycling while using the embedded queue. The queue starts when the WSGI serving process starts, resumes queued jobs before accepting requests, and shuts down with that process. Django management commands do not start transcription workers. `python manage.py runserver --noreload` is also available for local development; Gunicorn is the production entry point.

Frontend and admin use the same Django session, stored in SQLite for up to 12 hours and preserved across restarts. Signing out revokes that browser session for both interfaces. Disabling an account, deleting it, or changing its password blocks existing sessions. Sign in once after upgrading from the previous authentication system. Login is limited to five attempts per minute per client IP. `MAX_UPLOAD_MB` defaults to `500`; configure your reverse proxy's upload limit accordingly.

## Django administration

Open <http://localhost:8000/admin/> (with the trailing slash and no period) and sign in with a staff/superuser account. The standard Django admin includes users, groups, and transcription jobs.

To give someone access:

1. Open **Users → Add user**, enter a username, enable password authentication, and set a password.
2. Keep **Active** enabled. Regular people do not need **Staff status**, **Superuser status**, groups, or permissions to transcribe.
3. Give them their credentials and the frontend URL, <http://localhost:8000/>. They can upload recordings and return to their own history after signing in again.

Every active Django account can use the frontend. Only staff with the relevant permissions can enter Django admin; superusers can manage all accounts and recordings. Usernames can be up to 150 characters. Administrators can change passwords or deactivate accounts from the Users screen.

Jobs can be searched and filtered, and their settings, errors, and transcripts inspected. Job fields are read-only; retry and removal actions go through the queue so processing jobs cannot be deleted or edited. Admin actions are available to superusers. Django's standard CSRF protection applies to all admin forms. Existing API clients keep their current authentication flow.

Django's auth/admin/session tables share `jobs.sqlite3` with the existing queue table. The `TranscriptionJob` ORM model maps that existing table with `managed=False`, so migrations do not recreate it. Admin adds tables alongside the stored jobs. Jobs belong to permanent user IDs, not usernames: renaming an account retains its history, and deleting/recreating an account does not inherit someone else's recordings. The upgrade imports the original account and assigns matching legacy jobs to it; unknown legacy owners remain inaccessible to future accounts. All existing job IDs, transcripts, settings, and retry keys are preserved. The queue retains ownership of job state and its atomic concurrency controls.

## Background jobs and recovery

Uploads return a job ID immediately after the file is saved. Jobs run in queue order and expose **Queued → Processing → Completed / Failed** states. The frontend polls status every two seconds and shows queue position and active worker counts. You can submit another recording while existing jobs run, close the page, or sign out. Reopening the workspace restores the selected job and shows your most recent 50 recordings. Users can list, open, download, retry, or remove only their own jobs; another account's job ID returns HTTP 404. Upload retry keys are scoped to the user. Queue capacity and the two active transcription workers are shared across accounts; the displayed activity counts describe that shared queue. Transcripts remain stored until their job is removed; edits made in the transcript editor are only kept in the current page, so download edited text before leaving.

Queued jobs survive restarts and resume automatically. Jobs interrupted during processing are marked failed with an interruption message; use **Retry transcription** to restart them with their original recording and settings. They are not automatically retried, which avoids silently repeating provider requests. A retry starts from the beginning, not from a saved audio chunk.

Recordings are sent to Gemini. Successful recordings are deleted locally after the transcript is committed. Failed or interrupted recordings are retained for retry for 24 hours, with cleanup running at startup and about once per minute. After that, upload the file again. **Remove recording** deletes a completed or failed job and its stored result; queued jobs can be cancelled. Processing jobs must finish before removal.

## API

`GET /health` is public. Sign in with `POST /auth/login` using JSON `{"username":"admin","password":"your password"}` and keep the returned HTTP-only session cookie. `GET /auth/me` returns the username, permanent `user_id`, and upload limit, `GET /transcription/defaults` returns form defaults, and `POST /auth/logout` revokes the session. Browser writes must originate from the same site. The HTTP-only, Strict SameSite cookie now contains a Django database-backed session; origin, referer, and browser fetch-site checks protect browser writes without requiring a new CSRF token in existing API clients. All job endpoints require login and only expose jobs owned by the signed-in user:

| Endpoint | Behavior |
| --- | --- |
| `POST /jobs` | Save a multipart recording and settings; return HTTP 202 with job metadata and a `Location` status URL. |
| `GET /jobs?limit=50` | List recent jobs and global queue counts; limit 1–100. |
| `GET /jobs/{id}` | Read job status, queue position, error, retry availability, and Unix timestamps. |
| `GET /jobs/{id}/result` | Download the UTF-8 text transcript; HTTP 409 until completed. |
| `POST /jobs/{id}/retry` | Requeue a failed job with its original file and settings; HTTP 202. |
| `DELETE /jobs/{id}` | Cancel a queued job or remove a completed/failed one; HTTP 409 while processing. |

`POST /jobs` accepts the same multipart fields as the legacy API: `file`, `language_hint`, `model`, `fallback_model`, `chunk_minutes`, and `prompt`. Blank language enables auto-detection, blank fallback disables fallback, and chunk length `0` disables splitting.

An optional `Idempotency-Key` header (1–128 letters, digits, underscores, or hyphens) identifies one recording upload. Reusing it for the same account returns the existing job, even if the queue is full, without transcribing it again. Keys persist with the job record; removing the job also removes its key. The frontend generates one key per selected file and reuses it for upload retries. The existing SQLite database is migrated automatically when the updated server starts.

`POST /transcribe` remains compatible: it submits through the same persistent queue, waits for completion, and returns a plain-text attachment. It still needs a long HTTP connection and may hit proxy timeouts; use `/jobs` for background processing. Disconnecting does not cancel an accepted job.

`GET /openapi.json` publishes the request schema and defaults. `GET /docs` provides a local endpoint reference; it replaces FastAPI's interactive documentation.

## Code layout

```text
manage.py                    Django management entry point
config/                      Project settings, root URLs, WSGI, signing key
apps/
  accounts/                  Login/logout, account forms, auth backend, ownership
  transcriptions/            Recording and job application
    models.py                ORM mapping of the existing queue table
    admin.py                 Job inspection and protected queue actions
    migrations/              Existing migration history and upgrade steps
    services/
      jobs.py                Recording submission and provider calls
      queue.py               Persistent SQLite queue and worker threads
      runtime.py             Process lifecycle for the queue
      pipeline.py            Audio conversion and Gemini transcription engine
    templates/transcriptions/  Frontend Django templates
    static/transcriptions/     App JavaScript, theme, and CSS
    tests/                   Queue unit tests
common/                      Shared HTTP, middleware, and environment helpers
scripts/                     Standalone CLI entry points
  transcribe.py              python -m scripts.transcribe --help
  bootstrap_login.py         Initial legacy-bootstrap helper
tests/                       Project integration, migration, and frontend tests
```

`apps.accounts` and `apps.transcriptions` are registered Django applications. The accounts app uses Django's built-in User model, admin, and database sessions; it does not introduce a duplicate user model. Shared project helpers are in `common/`; application logic is contained inside its owning app.

The transcription app keeps its historical Django **app label** `web` while its Python package is `apps.transcriptions`. This preserves existing migration records, permissions, content types, and admin URLs such as `/admin/web/transcriptionjob/`. The directory move does not rename database tables or reset users and recordings. A migration updates the stored authentication backend path so existing sessions remain usable.

Namespaced frontend assets are served at `/static/transcriptions/`. Django's app static-file finder and WhiteNoise discover them directly; project-root `static/` and root-level authentication/queue modules are no longer used.

Existing `.env`, signing key, users, job IDs, files, results, and retry keys remain compatible. Django migrations retain the established queue table. Old FastAPI startup commands must use the Gunicorn command above. `DJANGO_SECRET_KEY` can supply a stable signing key; otherwise one is privately generated at `JOB_DATA_DIR/.django-secret-key` and reused across restarts. Keep this key and database together in backups.

Deployment references: [Django and Gunicorn](https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/gunicorn/), [WhiteNoise static files](https://whitenoise.readthedocs.io/en/stable/django.html).

## Verify

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py test --noinput
node --check apps/transcriptions/static/transcriptions/app.js
node tests/frontend.test.cjs
```

The tests stub Gemini transcription, so they do not send recordings or incur API charges. Actual transcription requires a valid Gemini API key and an accessible model. The standalone transcription command is now `python -m scripts.transcribe INPUT_FILE [options]`. It preserves the previous CLI options and output formats and does not require a web login. The reusable engine lives in the transcription app's services package.
