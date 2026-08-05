#!/usr/bin/env bash

set -euo pipefail

cd "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec celery -A app.celery_app.celery_app worker --loglevel="${CELERY_LOG_LEVEL:-INFO}"
