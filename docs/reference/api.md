# API reference

[Documentation index](../README.md)

`GET /health` is public. Sign in with `POST /auth/login` using JSON `{"username":"admin","password":"your password"}` and keep the returned HTTP-only session cookie. `GET /auth/me` returns the username, permanent `user_id`, and upload limit, `GET /transcription/defaults` returns form defaults, and `POST /auth/logout` revokes the session. Browser writes must originate from the same site. The HTTP-only, Strict SameSite cookie now contains a Django database-backed session; origin, referer, and browser fetch-site checks protect browser writes without requiring a new CSRF token in existing API clients. All job endpoints require login and only expose jobs owned by the signed-in user:

| Endpoint | Behavior |
| --- | --- |
| `GET /transcription/providers` | List enabled providers and public model defaults; requires login. |
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

## Related documentation

- [Provider selection and credentials](../guides/model-providers.md)
- [Transcribing recordings](../guides/transcriptions.md)
- [Accounts and administration](../guides/accounts.md)
