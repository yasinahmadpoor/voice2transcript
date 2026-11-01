# Testing and command-line tools

[Documentation index](../README.md)

Run all commands from the repository root.

## Verification

```sh
uv sync --locked
uv run --locked python manage.py check
uv run --locked python manage.py test --noinput
node --check static/js/theme.js
node --check apps/transcriptions/static/transcriptions/app.js
node tests/frontend.test.cjs
```

The tests stub Gemini transcription, so they do not send recordings or incur API charges. Actual transcription requires a valid Gemini API key and an accessible model. The standalone transcription command is now `uv run --locked python -m scripts.transcribe INPUT_FILE [options]`. It preserves the previous CLI options and output formats and does not require a web login. The reusable engine lives in the transcription app's services package.

## Related documentation

- [Dependency management](dependencies.md)
- [Local setup](../guides/getting-started.md)
- [Architecture](../reference/architecture.md)
