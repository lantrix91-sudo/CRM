#!/bin/sh
set -eu
python backend/manage.py migrate --noinput
python backend/manage.py createcachetable
python deploy/bootstrap-admin.py
python backend/manage.py collectstatic --noinput
cd backend
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers "${WEB_CONCURRENCY:-2}" --access-logfile - --error-logfile -
