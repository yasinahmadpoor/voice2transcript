# Architecture and code layout

[Documentation index](../README.md)

```text
docs/                        Guides, reference, and development documentation
pyproject.toml               Project metadata and direct dependencies
uv.lock                      Resolved dependency versions and hashes
.python-version              Default Python version for uv (3.12)
manage.py                    Django management entry point
config/                      Project settings, root URLs, WSGI, signing key
  settings/
    base.py                  Shared apps, middleware, database, and asset paths
    development.py           Debug pages and static asset refresh
    production.py            Docker/WSGI settings with debug disabled
    test.py                  Temporary data, isolated database, and test key
templates/                   Shared page layout for all apps
  base.html                  HTML shell with overridable content and head blocks
  includes/                  Shared navigation and footer
static/                      Shared assets loaded by the base template
  css/site.css               Site styles and theme colors
  js/theme.js                Saved theme and dark/light toggle
apps/
  accounts/                  Login/logout, account forms, auth backend, ownership
  transcriptions/            Recording and job application
    models.py                ORM mapping of the existing queue table
    admin.py                 Job inspection and protected queue actions
    migrations/              Existing migration history and upgrade steps
    services/
      jobs.py                Recording submission and provider calls
      queue.py               Persistent SQLite queue and worker threads
      execution.py           Per-job subprocess supervision and cancellation
      checkpoints.py         Durable chunk results and resume validation
      worker.py              Private Django-aware transcription subprocess
      runtime.py             Process lifecycle for the queue
      pipeline.py            Audio conversion and Gemini transcription engine
      providers.py           Provider lookup and compatible audio API adapter
    templates/transcriptions/  App pages extending the shared base template
    static/transcriptions/     Transcription-specific JavaScript
    tests/                   Queue unit tests
common/                      Shared HTTP, middleware, and environment helpers
scripts/                     Standalone CLI entry points
  transcribe.py              uv run --locked python -m scripts.transcribe --help
  bootstrap_login.py         Initial legacy-bootstrap helper
tests/                       Project integration, migration, and frontend tests
```

`apps.accounts` and `apps.transcriptions` are registered Django applications. The accounts app uses Django's built-in User model, admin, and database sessions; it does not introduce a duplicate user model. Shared project helpers are in `common/`; application logic is contained inside its owning app.

`ModelProvider` is a managed Django model for administrator-defined Gemini and OpenAI-compatible services. Credentials are encrypted through `common/credentials.py`. Jobs save the selected provider ID in their existing settings JSON; legacy `env` jobs resolve to the seeded Gemini record without changing their saved settings or checkpoint fingerprints. `seed_providers` creates that record once using the read-only unique `seed_key` added by migration 0008; subsequent runs preserve admin edits. The web app, worker, connection test, and CLI all read keys from the database. See the [provider guide](../guides/model-providers.md).

Meeting context is validated by `PromptContextForm` and composed by `services/prompts.py`. Submission stores raw meeting/participant fields, the original prompt, and an immutable `effective_prompt` in the existing job settings JSON; no new tables are required. The queue passes this snapshot to all provider adapters and checkpoint fingerprints. Older jobs without it retain their original prompt. An authenticated preview endpoint composes without a model call; a separate owner-scoped job context endpoint powers the read-only history view.

Each transcription runs in an isolated subprocess supervised by one queue thread. A cancellation request atomically changes `processing` to `cancelling`; supervision stops the job's process group, including audio conversion children, before marking it `cancelled` and releasing the worker slot. Completion updates only a still-processing job, preventing a late response from overwriting cancellation. Cancelled jobs retain their history and retry keys, and their recordings expire after 24 hours. Server shutdown stops subprocesses and preserves interrupted jobs for retry. The standalone CLI keeps its existing direct execution path.

The managed `JobCheckpoint` model stores a per-job chunk manifest and completed text in `job_checkpoints`. All three provider adapters use the same chunk runner. Each success commits atomically after checking that the job is still processing in the same attempt; cancelled or obsolete workers cannot append late results. SHA-256 fingerprints cover source audio and request configuration, while the manifest records chunk offsets and content digests. API keys are excluded. Retry rebuilds temporary audio and reuses matching saved text, then publishes the ordered final transcript. Explicit restart clears saved progress. Success, deletion, and recording expiry remove checkpoints. Migration 0007 also accommodates queue databases initialized before Django migrations run.

Environment settings are described in the [configuration guide](../guides/configuration.md). The settings package keeps `BASE_DIR` at the repository root; development and production use the same configured data directory and persistent signing key. The queue uses the selected settings' `DATA_DIR`, while tests use temporary storage.

Project-level `templates/base.html` provides the common page layout and includes `includes/navbar.html` and `includes/footer.html`. Each app keeps its own namespaced templates and extends `base.html`, filling the `content` block and adding its assets through `extra_head`. Other pages can override `title`, `header`, `footer`, and `extra_body` as needed. Django searches the project template directory and installed apps.

The transcription app keeps its historical Django **app label** `web` while its Python package is `apps.transcriptions`. This preserves existing migration records, permissions, content types, and admin URLs such as `/admin/web/transcriptionjob/`. The directory move does not rename database tables or reset users and recordings. A migration updates the stored authentication backend path so existing sessions remain usable.

Shared styles and theme behavior live in project-root `static/`, configured through `STATICFILES_DIRS`, and are loaded by `base.html` for every page that extends it. Transcription-specific JavaScript stays in the app at `/static/transcriptions/app.js`. Django's project and app static-file finders discover both locations, and WhiteNoise serves the assets. Docker includes both directories and runs `collectstatic` during the build.

Existing `.env`, signing key, users, job IDs, files, results, and retry keys remain compatible. Django migrations retain the established queue table. Old FastAPI startup commands must use the Gunicorn command in the local setup guide. `DJANGO_SECRET_KEY` can supply a stable signing key; otherwise one is privately generated at `JOB_DATA_DIR/.django-secret-key` and reused across restarts. Keep this key and database together in backups.

Deployment references: [Django and Gunicorn](https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/gunicorn/), [WhiteNoise static files](https://whitenoise.readthedocs.io/en/stable/django.html).

## Related documentation

- [Docker and deployment](../guides/deployment.md)
- [Testing and command-line tools](../development/testing.md)
