# Architecture and code layout

[Documentation index](../README.md)

```text
docs/                        Guides, reference, and development documentation
manage.py                    Django management entry point
config/                      Project settings, root URLs, WSGI, signing key
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
      runtime.py             Process lifecycle for the queue
      pipeline.py            Audio conversion and Gemini transcription engine
    templates/transcriptions/  App pages extending the shared base template
    static/transcriptions/     Transcription-specific JavaScript
    tests/                   Queue unit tests
common/                      Shared HTTP, middleware, and environment helpers
scripts/                     Standalone CLI entry points
  transcribe.py              python -m scripts.transcribe --help
  bootstrap_login.py         Initial legacy-bootstrap helper
tests/                       Project integration, migration, and frontend tests
```

`apps.accounts` and `apps.transcriptions` are registered Django applications. The accounts app uses Django's built-in User model, admin, and database sessions; it does not introduce a duplicate user model. Shared project helpers are in `common/`; application logic is contained inside its owning app.

Project-level `templates/base.html` provides the common page layout and includes `includes/navbar.html` and `includes/footer.html`. Each app keeps its own namespaced templates and extends `base.html`, filling the `content` block and adding its assets through `extra_head`. Other pages can override `title`, `header`, `footer`, and `extra_body` as needed. Django searches the project template directory and installed apps.

The transcription app keeps its historical Django **app label** `web` while its Python package is `apps.transcriptions`. This preserves existing migration records, permissions, content types, and admin URLs such as `/admin/web/transcriptionjob/`. The directory move does not rename database tables or reset users and recordings. A migration updates the stored authentication backend path so existing sessions remain usable.

Shared styles and theme behavior live in project-root `static/`, configured through `STATICFILES_DIRS`, and are loaded by `base.html` for every page that extends it. Transcription-specific JavaScript stays in the app at `/static/transcriptions/app.js`. Django's project and app static-file finders discover both locations, and WhiteNoise serves the assets. Docker includes both directories and runs `collectstatic` during the build.

Existing `.env`, signing key, users, job IDs, files, results, and retry keys remain compatible. Django migrations retain the established queue table. Old FastAPI startup commands must use the Gunicorn command above. `DJANGO_SECRET_KEY` can supply a stable signing key; otherwise one is privately generated at `JOB_DATA_DIR/.django-secret-key` and reused across restarts. Keep this key and database together in backups.

Deployment references: [Django and Gunicorn](https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/gunicorn/), [WhiteNoise static files](https://whitenoise.readthedocs.io/en/stable/django.html).

## Related documentation

- [Docker and deployment](../guides/deployment.md)
- [Testing and command-line tools](../development/testing.md)
