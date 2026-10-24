# Voice to Transcript

A FastAPI app that transcribes audio or video with Gemini and adds speaker labels. The web interface includes a private login, file upload and preview, a custom language hint, model and fallback model inputs, chunk length, an editable transcription prompt, an editable transcript, copying, and UTF-8 text downloads. Persian is the default language hint; leave it blank for auto-detection. The form loads its initial settings from the API, and **Reset to API defaults** restores them.

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

Open <http://localhost:8000>. For deployment, terminate HTTPS at your reverse proxy and set `SECURE_COOKIES=true` in `.env`. Forward the original host and scheme to Uvicorn; trust forwarded headers only from your proxy. Allow long request timeouts for transcription. The built-in login is designed for a small, single-account deployment with **one Uvicorn worker**. Sessions last 12 hours, are revoked on sign-out, and are cleared on server restart. Multiple workers or replicas require a shared session store. Login is limited to five attempts per minute per client IP.

`MAX_UPLOAD_MB` defaults to `500`. Upload validation enforces this limit; configure your reverse proxy's request-size limit too. Recordings are sent to Gemini, and local temporary audio files are removed when processing finishes. Transcripts are kept in the open browser page, so download your work before refreshing or signing out. A request already processing on the server can continue if the page closes or the user signs out.

## API

`GET /health` is public. Sign in with `POST /auth/login` using JSON `{"username":"admin","password":"your password"}` and keep the returned HTTP-only session cookie. `GET /auth/me` returns the signed-in account, `GET /transcription/defaults` returns the form defaults, and `POST /auth/logout` revokes the session. `POST /transcribe` requires the cookie and accepts the existing multipart fields: `file`, `language_hint`, `model`, `fallback_model`, `chunk_minutes`, and `prompt`. Its response remains a plain-text attachment. Browser writes must originate from the same site.

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
node --test tests/frontend.test.cjs
```

The tests stub Gemini transcription, so they do not send recordings or incur API charges. Actual transcription requires a valid Gemini API key and an accessible model. The existing command-line tool (`transcription_diarize.py`) is unchanged and does not require a web login.
