# Voice to Transcript

A Django application for transcribing audio and video with Gemini and speaker labels. It supports individual user accounts, private transcription history, batch uploads, persistent background jobs, and dark/light themes. Two transcriptions run at once by default.

Dependencies are managed with uv using `pyproject.toml` and `uv.lock`. Run `uv sync --locked` to set up the environment.

Detailed documentation is organized in [`docs/`](docs/README.md).

- [Run locally](docs/guides/getting-started.md)
- [Environment configuration](docs/guides/configuration.md)
- [Docker and deployment](docs/guides/deployment.md)
- [Accounts and administration](docs/guides/accounts.md)
- [Transcribing recordings](docs/guides/transcriptions.md)
- [API reference](docs/reference/api.md)
- [Architecture and code layout](docs/reference/architecture.md)
- [Dependency management](docs/development/dependencies.md)
- [Testing and command-line tools](docs/development/testing.md)
