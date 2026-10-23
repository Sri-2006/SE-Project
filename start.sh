#!/bin/sh
set -eu
cd "$(dirname "$0")"
test -d .venv || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py seed_demo
.venv/bin/python manage.py collectstatic --noinput
echo 'SupplyPilot: http://127.0.0.1:8000 — demo credentials are in .demo-credentials.txt'
exec .venv/bin/waitress-serve --threads=8 --listen=127.0.0.1:8000 supplypilot.wsgi:application
