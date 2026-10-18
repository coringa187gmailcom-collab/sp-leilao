#!/bin/sh
PORT="${PORT:-8080}"
exec gunicorn app:app --workers 1 --threads 8 --bind "0.0.0.0:${PORT}" --timeout 180
