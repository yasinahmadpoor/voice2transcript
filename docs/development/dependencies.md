# Dependency management

[Documentation index](../README.md)

Run these commands from the repository root with uv 0.9.9 or newer. Install uv using its [official installation guide](https://docs.astral.sh/uv/getting-started/installation/).

## Install and run

```sh
uv sync --locked
uv run --locked python manage.py check
uv run --locked python -m scripts.transcribe --help
```

`pyproject.toml` declares the direct dependencies and Python compatibility. `uv.lock` records the resolved versions and package hashes, and `.python-version` selects Python 3.12 by default. Keep all three files in Git. This is an application run from its source checkout, so `tool.uv.package = false` avoids building or installing the repository as a Python package.

The migration replaces `requirements.txt`. Existing `.venv` environments can be synchronized with `uv sync --locked`. This removes packages outside the project's dependency set; declare tools you need in `pyproject.toml` rather than installing them manually into this environment. There is currently no separate development dependency group; Django's built-in runner handles Python tests, and frontend tests use Node.js.

`--locked` verifies that the lockfile matches the project metadata. It prevents commands from silently regenerating the lockfile. Downloads are still needed for dependencies that are not already cached. On the selected Python 3.12 environment, the initial lockfile retains the dependency versions installed before the migration. Python 3.10 and 3.11 use compatible versions where required.

## Change dependencies

```sh
# Add or remove a direct dependency (replace PACKAGE with its name):
uv add PACKAGE
uv remove PACKAGE

# Update one dependency within its declared bounds:
uv lock --upgrade-package google-genai
uv sync --locked
```

After changing dependencies, run the [verification commands](testing.md), review the lockfile diff, and commit `pyproject.toml` and `uv.lock` together. To intentionally change the preferred Python version, use `uv python pin VERSION` and verify the Docker Python version and supported dependency versions too.

## Docker

The Docker build copies uv 0.9.9 from the official image and installs locked runtime dependencies with `uv sync --locked --no-dev`. `/app/.venv/bin` is on `PATH`; startup migrations and Gunicorn use that environment directly. Dependency downloads and resolution are not part of container startup. Rebuild the image after dependency changes.

See the official [project migration](https://docs.astral.sh/uv/guides/migration/pip-to-project/) and [Docker integration](https://docs.astral.sh/uv/guides/integration/docker/) guides.

## Related documentation

- [Local setup](../guides/getting-started.md)
- [Docker and deployment](../guides/deployment.md)
- [Testing and command-line tools](testing.md)
