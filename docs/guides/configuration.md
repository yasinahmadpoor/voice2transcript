# Environment configuration

[Documentation index](../README.md)

The former `config/settings.py` is split into a standard settings package:

```text
config/settings/
├── __init__.py
├── base.py
├── development.py
├── production.py
└── test.py
```

`base.py` contains shared apps, middleware, database configuration, authentication, upload limits, and template/static paths. It loads `.env` defaults without replacing variables already set in the process. Its `BASE_DIR` still points at the repository root.

## Choose an environment

| Module | Behavior | Default entry point |
| --- | --- | --- |
| `config.settings.development` | `DEBUG=True`, refreshed static assets, configured persistent data and signing key. | `manage.py` commands other than `test`. |
| `config.settings.production` | `DEBUG=False`, existing host/cookie protections, configured persistent data and signing key. | Docker and `config.wsgi`. |
| `config.settings.test` | `DEBUG=False`, temporary data directory, isolated SQLite test database, test-only signing key and local cookies. | `manage.py test` when no environment override is set. |

Select a module with `DJANGO_SETTINGS_MODULE`, or use Django's `--settings` option for management commands. An explicit environment variable takes precedence over the defaults above, and `--settings` takes precedence over that variable. If you previously set `DJANGO_SETTINGS_MODULE=config.settings`, replace it with one of the full module names above; the package itself is not a settings module.

Run commands from the repository root:

```sh
# Local development:
uv run --locked python manage.py runserver --noreload

# Explicit production check:
uv run --locked python manage.py check --settings=config.settings.production

# Explicit isolated test settings, even if the shell defaults to production:
uv run --locked python manage.py test --settings=config.settings.test --noinput

# Production serving:
DJANGO_SETTINGS_MODULE=config.settings.production uv run --locked gunicorn config.wsgi:application --config gunicorn.conf.py
```

You can also put `DJANGO_SETTINGS_MODULE` in `.env`. Leave it unset to use each entry point's default. Docker already sets it to production. When testing inside Docker, explicitly pass `--settings=config.settings.test` because the container environment otherwise selects production.

## Persistent data and secrets

Development and production honor `JOB_DATA_DIR`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, and `SECURE_COOKIES` as before. They use `./data` by default, including the existing `jobs.sqlite3` and persisted signing key. Docker keeps its existing mounted data volume. Choosing an environment does not create different accounts or automatically copy data between environments; set different `JOB_DATA_DIR` values if you want separate development and production data.

The test settings ignore the persisted data path and signing key, using a temporary directory and a test-only key. The Django test database is in memory, and the temporary directory is cleaned up when the process exits. The queue reads its data directory from the selected Django settings. Migration and Gunicorn integration tests explicitly choose production settings with their own temporary data directories.

Production retains the current HTTP/HTTPS configuration: set `SECURE_COOKIES=true` behind HTTPS and configure your deployment host and trusted proxy as described in the [deployment guide](deployment.md). Deploy with the production module; development enables debug pages.

See Django's [settings documentation](https://docs.djangoproject.com/en/5.2/topics/settings/) for module selection and settings imports.

## Related documentation

- [Local setup](getting-started.md)
- [Docker and deployment](deployment.md)
- [Testing](../development/testing.md)
