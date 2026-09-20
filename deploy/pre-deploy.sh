#!/bin/sh
set -eu
python backend/manage.py migrate --noinput
python backend/manage.py createcachetable
python deploy/bootstrap-admin.py
