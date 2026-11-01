# Documentation

[Project overview](../README.md)

## Guides

- [Local setup](guides/getting-started.md): dependencies, configuration, and first login.
- [Docker and deployment](guides/deployment.md): containers, persistent storage, proxy settings, and worker limits.
- [Accounts and administration](guides/accounts.md): adding users, access control, and private history.
- [Transcribing recordings](guides/transcriptions.md): batch uploads, settings, results, retries, and recovery.

## Reference

- [API](reference/api.md): authentication, job endpoints, multipart fields, and retry keys.
- [Architecture and code layout](reference/architecture.md): Django apps, shared templates and assets, and migration compatibility.

## Development

- [Dependency management](development/dependencies.md): uv setup, lockfiles, and dependency updates.
- [Testing and command-line tools](development/testing.md): verification commands and standalone transcription.

Shell commands in these guides run from the repository root, unless stated otherwise. Start environment configuration with [`.env.example`](../.env.example).
