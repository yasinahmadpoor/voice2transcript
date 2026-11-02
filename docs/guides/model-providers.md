# Model providers

[Documentation index](../README.md)

Superusers can configure providers at **Django admin → Model providers**. Ordinary users can select enabled providers in the frontend's collapsed **Transcription settings** section.

## Seed the initial Gemini provider

After migrations, run:

```sh
uv run --locked python manage.py seed_providers
```

Docker runs this command automatically after migrations. It creates a normal
Gemini database record with its name, kind, connection settings, primary model,
fallback model, enabled/default flags, and encrypted API key. Direct Google
Gemini uses a blank base URL to select the native Google endpoint and Files API.
The initial models are `gemini-3.1-pro-preview` and `gemini-2.5-flash`.

The command imports `GEMINI_API_KEY`, or `GOOGLE_API_KEY` if the former is blank,
only when creating the record. The key is never written into a source fixture
or printed. A stable, read-only seed identifier prevents duplicates even after
renaming the provider. Repeating the command preserves all admin edits,
including rotated or cleared keys and disabled status. An existing selected
default remains selected; otherwise a newly seeded, enabled Gemini becomes the
default. A conflicting provider name is preserved and the new record gets a
different name.

Without an initial key, Gemini is created disabled. Add its key and enable it
in admin; enabling the first provider also selects it as the default. If no
enabled default exists, transcription defaults return a clear setup error.

After seeding, the web app, workers, connection tests, and CLI use saved database
credentials, never an environment-key fallback. Changing or removing an API key
in `.env` does not change an existing database provider. Manage its key and model
settings in admin. You may remove the bootstrap API key from `.env` after a
successful import; keep other deployment settings and the persistent signing key.

## Add a provider

1. Sign in at `/admin/` with a superuser account and open **Model providers → Add**.
2. Enter a name and choose **Gemini** or **OpenAI-compatible audio transcription**.
3. For direct Google Gemini, leave **Base URL** blank and enter its Google API key. On an existing provider, leaving the key field blank keeps its saved key. For a Gemini proxy, enter its API root and its own API key. When changing a saved Gemini provider to a different host, re-enter the key for that host.
4. For a compatible service, enter its API root, such as `https://api.example.com/v1`, and its API key. Local services that require no authentication can leave the key blank. The adapter appends `/audio/transcriptions`; the service must implement that audio endpoint. A chat-only completion endpoint is insufficient.
5. Enter an audio-capable model identifier and, optionally, a fallback model from the same provider. OpenAI-compatible services must support JSON responses with a `text` field; the app requests this format automatically.
6. Keep **Is enabled** checked and save. The first provider added through admin becomes the default when no configured default exists.

To switch the default later, select exactly one enabled provider in the list and run **Use selected provider as the default**. To return to the seeded Gemini provider, select a row and run **Use seeded Gemini as the default**. Provider changes take effect without restarting the server.

Providers are disabled rather than deleted, preserving their identity for queued and failed jobs. Choose another default before disabling the current default. Disabled providers disappear from the frontend and cannot execute new work or retries until enabled again.

## Test a connection

Open a saved provider in admin and click **Test connection**. After editing a key,
URL, or model, use **Save and continue editing** first. The button uses saved
settings and is disabled while the form has unsaved changes or a test is running.
Disabled providers can also be tested before enabling them.

The test sends a bundled 2.2-second synthetic English recording saying “Hello,
this is a transcription test.” through the same audio adapter as normal jobs.
It tests the saved primary model only, with no fallback. Direct Google Gemini
uses the existing Files API path; Gemini proxies use inline audio; compatible
providers use `/audio/transcriptions`. A successful result means the model
returned a nonempty transcript; it does not measure transcription accuracy or
guarantee support for longer recordings or every language.

The result appears on the page with elapsed time. Errors distinguish rejected
credentials, access restrictions, an unknown endpoint/model, quota or payment
issues, network errors, invalid responses, and timeouts where the provider's
response allows it. Tests stop after 30 seconds, with at most one active test
per server process. They do not create jobs, history, or checkpoints, and do not
change provider settings. The sample transcript is discarded. Only superusers
can run tests; keys and raw provider errors are never returned to the browser.

Each click makes a real audio request and may incur a small provider charge.
Stopping a timed-out local test cannot cancel work already accepted by a remote
provider. The sample is stored in
`apps/transcriptions/fixtures/connection-test.wav`; generation instructions and
provenance are in that directory's README. No speech-generation dependency is
needed in production.

## Gemini through Metis

Create a key in [Metis Console](https://console.metisai.ir/) and add a provider with these settings:

| Field | Value |
| --- | --- |
| Name | Metis Gemini |
| Kind | Gemini |
| Base URL | `https://api.metisai.ir` |
| API key | The key issued by Metis |
| Model | A Gemini model supported by your Metis account; their documentation illustrates `gemini-2.5-pro`. |
| Fallback model | Optional; another Gemini model supported by that provider. |

Use the Gemini root above, rather than Metis's OpenAI root `/openai/v1`. The [Metis Gemini documentation](https://docs.metisai.ir/api/wrapper/gemini/) describes this native Gemini connection. Gemini continues using its existing response handling without an explicit response format.

Custom Gemini URLs use `v1beta` `generateContent` with inline WAV audio, without uploading files to Google's Files API. With splitting enabled, chunks are capped at five minutes and 10 MB raw audio; encoded audio and prompt are checked against a 20 MB request budget. Smaller user-selected chunks are honored. Disabling splitting requires the whole normalized recording to fit those limits. Direct Google Gemini keeps its existing Files API pipeline.

Protocol tests use the real Gemini SDK with a mocked HTTP transport. Live Metis audio support and model access still need a test with your own key; no paid provider request is made by the test suite.

## Jobs and the frontend

The provider dropdown appears under **Transcription settings**. Selecting a provider fills its model and fallback defaults; users can still edit those model names. Batch uploads use one snapshot of the selected settings for all files. **Reset to API defaults** restores the default provider and its models loaded when the page opened. Reload the page after an administrator changes provider configuration to refresh the catalog and defaults.

Each submitted job stores a provider ID with its transcription settings, never an API key. Switching the default does not reroute queued jobs or retries. Existing jobs without a provider ID, or with the old `env` ID, resolve to the seeded Gemini record. Their stored settings and checkpoint fingerprints remain unchanged. Editing a provider's connection details or key applies to subsequent executions of jobs assigned to that provider; key rotation does not require reuploading recordings. All providers share the same queue and concurrency limit.

Saved chunk progress is compatible with API key rotation. Changing a provider's kind or base URL invalidates resume for jobs that already have saved chunks; those jobs require **Restart from beginning** to avoid mixing results from different configurations. Changing provider model defaults does not change the model settings already stored with a job.

The compatible adapter uses multipart `POST /audio/transcriptions`. It sends the selected model, `response_format=json`, and supported language/prompt options. The app extracts the `text` field and preserves its content, including JSON text produced in response to a prompt. This API response envelope is separate from instructions about the transcript's content. Speaker labels depend on the model; a generic speech-to-text model does not gain diarization just from the prompt. Models requiring the separate `diarized_json` format are not supported by this adapter. See the [audio API reference](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create) for the protocol; compatible services may support fewer options.

Compatible uploads use normalized WAV chunks of at most ten minutes and 25 MB each. A smaller user-selected chunk length is honored. Setting chunk length to zero disables splitting; recordings whose normalized WAV exceeds 25 MB then fail with a request to enable chunking. Fallback runs only when the primary model returns empty output. HTTP failures are not automatically retried against a different provider.

## Credentials and access

Only superusers can view or change provider administration. Saved API keys are encrypted in SQLite using a key derived from the persistent Django signing key. The edit form never displays a saved key: leave **API key** blank to retain it, enter a replacement to rotate it, or check **Clear API key** to remove it. Key values and base URLs are excluded from the frontend catalog, OpenAPI defaults, and job settings. Provider response bodies and SDK credentials are excluded from queue error logs.

Keep the existing `.django-secret-key`, or the configured `DJANGO_SECRET_KEY`, with database backups. Changing that key makes saved provider credentials unreadable until they are re-entered. The CLI also requires migrations and seeding, then reads the seeded Gemini provider's saved key and model defaults. Explicit `--model` and `--fallback-model` values override those saved defaults for that command. It continues to support Gemini transcription and structured output; it does not switch to an OpenAI-compatible provider when the web default changes.

## API

`GET /transcription/providers` requires login and returns enabled database providers with public IDs, names, kinds, and model defaults. There is no separate environment option. `GET /transcription/defaults` includes a numeric-string `provider` ID. Submit that value with the multipart recording. Omitting the field selects the current default; old clients sending `env` are mapped to the seeded Gemini provider. Unknown or disabled IDs return HTTP 422. If no default is configured, defaults and submissions that omit a provider return HTTP 503 with setup instructions. Provider configuration itself is managed through Django admin.

## Related documentation

- [Accounts and administration](accounts.md)
- [API reference](../reference/api.md)
- [Deployment](deployment.md)
