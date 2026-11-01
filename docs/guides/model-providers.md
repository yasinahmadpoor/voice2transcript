# Model providers

[Documentation index](../README.md)

Superusers can configure providers at **Django admin → Model providers**. Ordinary users can select enabled providers in the frontend's collapsed **Transcription settings** section.

## Add a provider

1. Sign in at `/admin/` with a superuser account and open **Model providers → Add**.
2. Enter a name and choose **Gemini** or **OpenAI-compatible audio transcription**.
3. For direct Google Gemini, leave **Base URL** blank. Enter a Google API key, or leave it blank to use `GEMINI_API_KEY` / `GOOGLE_API_KEY` from the environment. For a Gemini proxy, enter its API root and its own API key; environment credentials are never used with a custom Gemini URL. When changing a saved Gemini provider to a different host, re-enter the key for that host.
4. For a compatible service, enter its API root, such as `https://api.example.com/v1`, and its API key. Local services that require no authentication can leave the key blank. The adapter appends `/audio/transcriptions`; the service must implement that audio endpoint. A chat-only completion endpoint is insufficient.
5. Enter an audio-capable model identifier and, optionally, a fallback model from the same provider. OpenAI-compatible services must support JSON responses with a `text` field; the app requests this format automatically.
6. Keep **Is enabled** checked and save. The first provider added through admin becomes the default when no configured default exists.

To switch the default later, select exactly one enabled provider in the list and run **Use selected provider as the default**. To return to the original environment-based Gemini setup, select a row and run **Restore environment Gemini as the default**. Provider changes take effect without restarting the server.

Providers are disabled rather than deleted, preserving their identity for queued and failed jobs. Choose another default, or restore environment Gemini, before disabling the current default. Disabled providers disappear from the frontend and cannot execute new work or retries until enabled again.

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

Each submitted job stores a provider ID with its transcription settings, never an API key. Switching the default does not reroute queued jobs or retries. Existing jobs without a provider ID continue using environment Gemini. Editing a provider's connection details or key applies to subsequent executions of jobs assigned to that provider; key rotation does not require reuploading recordings. All providers share the same queue and concurrency limit.

The compatible adapter uses multipart `POST /audio/transcriptions`. It sends the selected model, `response_format=json`, and supported language/prompt options. The app extracts the `text` field and preserves its content, including JSON text produced in response to a prompt. This API response envelope is separate from instructions about the transcript's content. Speaker labels depend on the model; a generic speech-to-text model does not gain diarization just from the prompt. Models requiring the separate `diarized_json` format are not supported by this adapter. See the [audio API reference](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create) for the protocol; compatible services may support fewer options.

Compatible uploads use normalized WAV chunks of at most ten minutes and 25 MB each. A smaller user-selected chunk length is honored. Setting chunk length to zero disables splitting; recordings whose normalized WAV exceeds 25 MB then fail with a request to enable chunking. Fallback runs only when the primary model returns empty output. HTTP failures are not automatically retried against a different provider.

## Credentials and access

Only superusers can view or change provider administration. Saved API keys are encrypted in SQLite using a key derived from the persistent Django signing key. The edit form never displays a saved key: leave **API key** blank to retain it, enter a replacement to rotate it, or check **Clear API key** to remove it. Key values and base URLs are excluded from the frontend catalog, OpenAPI defaults, and job settings. Provider response bodies and SDK credentials are excluded from queue error logs.

Keep the existing `.django-secret-key`, or the configured `DJANGO_SECRET_KEY`, with database backups. Changing that key makes saved provider credentials unreadable until they are re-entered. No provider record is required to keep using the existing `.env` Gemini configuration. The standalone CLI continues to use environment Gemini.

## API

`GET /transcription/providers` requires login and returns enabled providers with public IDs, names, kinds, and model defaults. `GET /transcription/defaults` includes a `provider` field. Submit that value with the multipart recording: `env` selects environment Gemini; a numeric string selects a configured provider. Omitting the field selects the current default. Unknown or disabled IDs return HTTP 422. Provider configuration itself is managed through Django admin.

## Related documentation

- [Accounts and administration](accounts.md)
- [API reference](../reference/api.md)
- [Deployment](deployment.md)
