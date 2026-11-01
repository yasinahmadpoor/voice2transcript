# Run locally

[Documentation index](../README.md)

Run all commands from the repository root.

Requires Python 3.10+ and `ffmpeg` / `ffprobe` on your PATH.

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` from `.env.example` if you don't have one, and set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`). Keep your existing `.env` if it already contains a key. Configure your account:

```sh
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --noinput
gunicorn config.wsgi:application --config gunicorn.conf.py --bind 127.0.0.1:8000
```

Open <http://localhost:8000> and sign in using the Django account you created. `createsuperuser` prompts for a username and password; no default password is supplied. Additional people can be added through Django admin. Existing installations with `APP_USERNAME` and `APP_PASSWORD_HASH` are imported once during the user-ownership migration, preserving the original login. After migration, passwords and account status are managed in Django admin or with `python manage.py changepassword USERNAME`; `.env` credentials no longer override database accounts. `python -m scripts.bootstrap_login` remains an initial legacy-bootstrap helper for a fresh database.

## Related documentation

- [Docker and deployment](deployment.md)
- [Accounts and administration](accounts.md)
- [Transcribing recordings](transcriptions.md)
