#!/bin/sh
set -eu
umask 077

python manage.py migrate --noinput
exec "$@"
