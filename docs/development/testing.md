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
node tests/frontend.test.cjs
node tests/provider-test.test.cjs
```

The tests stub providers or run a local HTTP transcription server, so they do not contact external model services or incur API charges. Process integration tests verify cancellation, conversion-child termination, worker-slot reuse, temporary-file cleanup, checkpoint recovery across queue restarts, and explicit restart after incompatible provider changes. Adapter tests cover resume and API key rotation for all three provider paths. Admin connection tests cover audio routing, credentials, permissions, CSRF, safe diagnostics, and process deadline cleanup without creating jobs. The separate provider-button frontend tests check loading state, duplicate requests, unsaved settings, failures, and expired sessions. Actual transcription requires a valid provider configuration and an accessible model. The standalone transcription command is now `uv run --locked python -m scripts.transcribe INPUT_FILE [options]`. It preserves the previous CLI options and output formats and does not require a web login. The CLI requires migrations and `seed_providers` and reads the seeded Gemini record's encrypted key and model defaults. Seeding tests cover repeated runs, existing defaults, cleared keys, missing-key setup, database-only runtime lookup, CLI overrides, and legacy checkpoint reuse. The reusable engine lives in the transcription app's services package.

## Related documentation

- [Dependency management](dependencies.md)
- [Local setup](../guides/getting-started.md)
- [Architecture](../reference/architecture.md)
