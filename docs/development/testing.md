# Testing and command-line tools

[Documentation index](../README.md)

Run all commands from the repository root.

## Verification

```sh
uv sync --locked
uv run --locked python manage.py check
uv run --locked python manage.py test --settings=config.settings.test --noinput
node --check static/js/theme.js
node --check apps/transcriptions/static/transcriptions/app.js
node --check apps/transcriptions/static/transcriptions/meeting-context.js
node tests/frontend.test.cjs
node tests/provider-test.test.cjs
```

The tests stub providers or run a local HTTP transcription server, so they do not contact external model services or incur API charges. Process integration tests verify cancellation, conversion-child termination, worker-slot reuse, temporary-file cleanup, checkpoint recovery across queue restarts, and explicit restart after incompatible provider changes. Adapter tests cover resume and API key rotation for all three provider paths. Admin connection tests cover audio routing, credentials, permissions, CSRF, safe diagnostics, and process deadline cleanup without creating jobs. The separate provider-button frontend tests check loading state, duplicate requests, unsaved settings, failures, and expired sessions. Actual transcription requires a valid provider configuration and an accessible model. The standalone transcription command is now `uv run --locked python -m scripts.transcribe INPUT_FILE [options]`. It preserves the previous CLI options and output formats and does not require a web login. The CLI requires migrations and `seed_providers` and reads the seeded Gemini record's encrypted key and model defaults. Seeding tests cover repeated runs, existing defaults, cleared keys, missing-key setup, database-only runtime lookup, CLI overrides, and legacy checkpoint reuse. Meeting context tests cover limits, preview/submission parity, all provider routes, private history, immutable retries and checkpoint reuse, frontend batch snapshots, and stale responses after logout or editing. The reusable engine lives in the transcription app's services package.

## Related documentation

- [Dependency management](dependencies.md)
- [Local setup](../guides/getting-started.md)
- [Architecture](../reference/architecture.md)

Export tests (`tests/test_exports.py`) exercise both real document renderers and
the bounded child worker, Word text/RTL properties and embedded font recovery,
PDF font embedding, literal markup and blocked resources, ownership, validation,
immutable saved text, busy slots, and timeout cleanup. Frontend tests verify edited
text, download names, duplicate prevention, errors, expired sessions, and stale
responses after logout or changing the selected recording. Visual QA should render
synthetic Persian/English Word and PDF samples, including multiple pages, before
changing document styles. No provider call is needed.

For local PDF generation on Debian/Ubuntu install the native libraries with
`sudo apt-get install libpango-1.0-0 libpangoft2-1.0-0` before `uv sync --locked`.
Large Persian transcript regression tests also exercise worker input larger than
64 KB for both formats. Workers read from a private anonymous temporary file,
so input cannot stall on a partially written pipe while the parent polls for
cancellation or timeout.

The Docker image installs these libraries automatically; fonts and their license
are in `apps/transcriptions/assets/fonts/`.
