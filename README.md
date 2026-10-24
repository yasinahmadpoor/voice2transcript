# Voice to Transcript

A FastAPI app that transcribes audio or video with Gemini and adds speaker labels. Recordings run as persistent background jobs with **two active transcriptions** by default. The interface includes login, upload and preview, language and model settings, an editable prompt, recent job history, status updates, retries, and editable transcripts with copying and text downloads. Persian is the default language hint; leave it blank for auto-detection. Settings start collapsed and load defaults from the API.

## Run locally

Requires Python 3.10+ and `ffmpeg` / `ffprobe` on your PATH.

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` from `.env.example` if you don't have one, and set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`). Keep your existing `.env` if it already contains a key. Configure your account:

```sh
python setup_login.py --username admin
uvicorn transcription_api:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000> and sign in using your chosen username and password. The setup command prompts for a password of at least 12 characters, stores a salted PBKDF2 hash, and preserves other `.env` settings. Run it again and restart the server to change credentials. No default password is supplied.

## Docker

Configure `.env` and your account as above, then:

```sh
docker compose up --build -d
```

Open <http://localhost:8000>. The `transcription-data` Docker volume stores SQLite job records, uploaded recordings, and completed transcript text under `/app/data`. Keep this volume when recreating the container; `docker compose down -v` removes its stored jobs and results. Local runs use `./data` by default; `JOB_DATA_DIR` can select another persistent directory. Job data is excluded from Git and Docker build context.

For deployment, terminate HTTPS at your reverse proxy and set `SECURE_COOKIES=true`. Forward the original host and scheme to Uvicorn; trust forwarded headers only from your proxy. The app is designed for a small, single-account deployment with **one Uvicorn worker**. A process lock prevents multiple queue processes from sharing a data directory. `TRANSCRIPTION_WORKERS` controls the dedicated transcription threads inside that process, independently of the web server's thread pool. It defaults to `2` (range 1–16). `MAX_PENDING_JOBS` defaults to `20`, including queued and processing jobs; a full queue returns HTTP 429. All jobs, including legacy `/transcribe` requests, share these workers.

Sessions last 12 hours, are revoked on sign-out, and are cleared on server restart. Sign in again after a restart to reopen your jobs. Login is limited to five attempts per minute per client IP. `MAX_UPLOAD_MB` defaults to `500`; configure your reverse proxy's upload limit accordingly.

## Background jobs and recovery

Uploads return a job ID immediately after the file is saved. Jobs run in queue order and expose **Queued → Processing → Completed / Failed** states. The frontend polls status every two seconds and shows queue position and active worker counts. You can submit another recording while existing jobs run, close the page, or sign out. Reopening the workspace restores the selected job and shows your most recent 50 recordings. Transcripts remain stored until their job is removed; edits made in the transcript editor are only kept in the current page, so download edited text before leaving.

Queued jobs survive restarts and resume automatically. Jobs interrupted during processing are marked failed with an interruption message; use **Retry transcription** to restart them with their original recording and settings. They are not automatically retried, which avoids silently repeating provider requests. A retry starts from the beginning, not from a saved audio chunk.

Recordings are sent to Gemini. Successful recordings are deleted locally after the transcript is committed. Failed or interrupted recordings are retained for retry for 24 hours, with cleanup running at startup and about once per minute. After that, upload the file again. **Remove recording** deletes a completed or failed job and its stored result; queued jobs can be cancelled. Processing jobs must finish before removal.

## API

`GET /health` is public. Sign in with `POST /auth/login` using JSON `{"username":"admin","password":"your password"}` and keep the returned HTTP-only session cookie. `GET /auth/me` returns the account, `GET /transcription/defaults` returns form defaults, and `POST /auth/logout` revokes the session. Browser writes must originate from the same site. All job endpoints require login and only expose jobs owned by the configured account:

| Endpoint | Behavior |
| --- | --- |
| `POST /jobs` | Save a multipart recording and settings; return HTTP 202 with job metadata and a `Location` status URL. |
| `GET /jobs?limit=50` | List recent jobs and global queue counts; limit 1–100. |
| `GET /jobs/{id}` | Read job status, queue position, error, retry availability, and Unix timestamps. |
| `GET /jobs/{id}/result` | Download the UTF-8 text transcript; HTTP 409 until completed. |
| `POST /jobs/{id}/retry` | Requeue a failed job with its original file and settings; HTTP 202. |
| `DELETE /jobs/{id}` | Cancel a queued job or remove a completed/failed one; HTTP 409 while processing. |

`POST /jobs` accepts the same multipart fields as the legacy API: `file`, `language_hint`, `model`, `fallback_model`, `chunk_minutes`, and `prompt`. Blank language enables auto-detection, blank fallback disables fallback, and chunk length `0` disables splitting.

`POST /transcribe` remains compatible: it submits through the same persistent queue, waits for completion, and returns a plain-text attachment. It still needs a long HTTP connection and may hit proxy timeouts; use `/jobs` for background processing. Disconnecting does not cancel an accepted job.

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
node --test tests/frontend.test.cjs
```

The tests stub Gemini transcription, so they do not send recordings or incur API charges. Actual transcription requires a valid Gemini API key and an accessible model. The existing command-line tool (`transcription_diarize.py`) is unchanged and does not require a web login.
