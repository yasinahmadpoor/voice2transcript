# API reference

[Documentation index](../README.md)

`GET /health` is public. Sign in with `POST /auth/login` using JSON `{"username":"admin","password":"your password"}` and keep the returned HTTP-only session cookie. `GET /auth/me` returns the username, permanent `user_id`, and upload limit, `GET /transcription/defaults` returns form defaults, and `POST /auth/logout` revokes the session. Browser writes must originate from the same site. The HTTP-only, Strict SameSite cookie now contains a Django database-backed session; origin, referer, and browser fetch-site checks protect browser writes without requiring a new CSRF token in existing API clients. All job endpoints require login and only expose jobs owned by the signed-in user:

| Endpoint | Behavior |
| --- | --- |
| `GET /transcription/providers` | List enabled providers and public model defaults; requires login. |
| `POST /transcription/prompt-preview` | Validate JSON prompt/context and return `effective_prompt`; requires login, makes no model call, and creates no job. |
| `POST /jobs` | Save a multipart recording and settings; return HTTP 202 with job metadata and a `Location` status URL. |
| `GET /jobs?limit=50` | List recent jobs and global queue counts; limit 1–100. |
| `GET /jobs/{id}` | Read job status, queue position, error, retry/resume availability, chunk progress, and Unix timestamps. |
| `GET /jobs/{id}/result` | Download the UTF-8 text transcript; HTTP 409 until completed. |
| `GET /jobs/{id}/context` | Read the owner's saved `meeting_description`, `participants`, original `prompt`, and `effective_prompt`; another account gets 404. |
| `POST /jobs/{id}/retry` | Requeue a failed or cancelled job with its original file/settings and resume saved chunks; HTTP 202. Send JSON `{"restart":true}` to discard progress and restart. Empty requests remain supported. |
| `POST /jobs/{id}/cancel` | Cancel a queued or processing job; HTTP 202. Processing jobs become `cancelling` until their local process stops, then `cancelled`. Repeated cancellation is safe. Completed or failed jobs return HTTP 409. |
| `DELETE /jobs/{id}` | Remove a queued, completed, failed, or cancelled job and its recording/result; HTTP 409 while processing or cancelling. |

`POST /jobs` accepts multipart fields `file`, `provider`, `language_hint`, `model`, `fallback_model`, `chunk_minutes`, and `prompt`. Provider IDs come from the database catalog; omitting `provider` selects the saved default. The legacy `env` value maps to the seeded Gemini record. There is no environment-key fallback. If no enabled default exists, defaults and submissions that omit a provider return HTTP 503 with setup instructions. Blank language enables auto-detection, blank fallback disables fallback, and chunk length `0` disables splitting.

Optional multipart fields `meeting_description` (text, up to 5,000 characters)
and `participants` (a JSON-encoded array) provide meeting context. For example:

```json
[{"name":"Sara","description":"Project lead"},{"name":"Ali","description":""}]
```

At most 20 participants are accepted, with names up to 100 characters and
descriptions up to 1,000 characters. Blank rows are ignored; a description
without a name is rejected. Invalid JSON, types, or lengths return HTTP 422.
The `prompt` field remains the original instructions. The app saves a compiled
`effective_prompt` with each job; clients cannot supply or override this field.
It is reused for every chunk and every retry/resume/restart. Empty context
preserves the original prompt exactly, including for legacy jobs.

Preview JSON accepts `prompt`, `meeting_description`, and a native `participants`
array, with all three optional. The preview uses the same validation/composer as
submission. Its response is `{"effective_prompt":"..."}`. Provider-specific
language handling still applies afterward. Saved context is fetched on demand,
not included in polling lists or public metadata. Deleting the job deletes its
context together with the settings.

An optional `Idempotency-Key` header (1–128 letters, digits, underscores, or hyphens) identifies one recording upload. Reusing it for the same account returns the existing job, even if the queue is full, without transcribing it again. Keys persist with the job record; removing the job also removes its key. The frontend generates one key per selected file and reuses it for upload retries. The existing SQLite database is migrated automatically when the updated server starts.

`POST /transcribe` remains compatible: it submits through the same persistent queue, waits for completion, and returns a plain-text attachment. It still needs a long HTTP connection and may hit proxy timeouts; use `/jobs` for background processing. Disconnecting does not cancel an accepted job.

Cancelling preserves the job's history and idempotency key. Its recording remains available for explicit retry for 24 hours, using the original settings. Cancelling an active job stops its isolated local process and conversion children, closes its network connection, and frees its slot once it reaches `cancelled`. A provider may still finish or charge for a request it already received; this endpoint does not call a provider-specific remote cancellation API. A cancelled legacy `/transcribe` request returns HTTP 409.

Job metadata includes `progress: {"completed_chunks": 3, "total_chunks": 10}`, `resumable`, and `restart_required`. Counts are zero before audio preparation and after checkpoint cleanup; saved transcript chunks are never included in status responses. Failed/cancelled jobs retain checkpoints alongside recordings for 24 hours. Completed jobs keep their final transcript and discard checkpoints. Deleting a job removes its checkpoints.

Resume validates the source recording, actual chunk boundaries/content, and provider/model/prompt settings. Incompatible saved progress is preserved and the job fails with `restart_required: true`; explicitly restart to proceed. API key rotation is compatible. An unfinished/uncommitted chunk may be requested again. With splitting disabled there is no intermediate progress. Existing failed jobs without checkpoints start from the beginning.

`GET /openapi.json` publishes the request schema and defaults. `GET /docs` provides a local endpoint reference; it replaces FastAPI's interactive documentation.

## Related documentation

- [Provider selection and credentials](../guides/model-providers.md)
- [Transcribing recordings](../guides/transcriptions.md)
- [Accounts and administration](../guides/accounts.md)
