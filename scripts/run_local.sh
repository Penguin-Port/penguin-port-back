#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [[ ! -f ".venv/bin/activate" ]]; then
  echo "[0/3] 가상환경 생성 및 의존성 설치"
  python3 -m venv --system-site-packages .venv
fi

source .venv/bin/activate

if [[ -f ".env" ]]; then
  set -a
  source .env
  set +a
fi

if ! python3 -c "import fastapi, sqlalchemy, alembic, jwt, openai" >/dev/null 2>&1; then
  echo "의존성이 없어 requirements.txt를 설치합니다."
  python3 -m pip install -r requirements.txt
fi

export DATABASE_URL="${DATABASE_URL:-sqlite:////private/tmp/smartpass-manual.sqlite3}"
export JWT_SECRET="${JWT_SECRET:-local-test-secret-change-this}"
export DEMO_KEY="${DEMO_KEY:-demo-key}"
export DEMO_OTP_CODE="${DEMO_OTP_CODE:-123456}"
export EXPIRE_INTERVAL_SECONDS="${EXPIRE_INTERVAL_SECONDS:-60}"
export OPENAI_MODEL="${OPENAI_MODEL:-gpt-5-mini}"
export OPENAI_TIMEOUT_SECONDS="${OPENAI_TIMEOUT_SECONDS:-20}"

# 기본 로컬 DB는 매번 깨끗하게 시작합니다.
# RESET_DB=0 으로 실행하면 기존 데이터를 유지합니다.
if [[ "${RESET_DB:-1}" == "1" && "${DATABASE_URL}" == "sqlite:////private/tmp/smartpass-manual.sqlite3" ]]; then
  rm -f /private/tmp/smartpass-manual.sqlite3
fi

echo "[1/3] Alembic migration"
alembic upgrade head

echo "[2/3] Demo seed"
python3 -m app.seed

if [[ "${RUN_SERVER:-1}" != "1" ]]; then
  echo "[3/3] 서버 실행 생략 (RUN_SERVER=0)"
  exit 0
fi

echo "[3/3] FastAPI server: http://127.0.0.1:8000"
exec uvicorn app.main:app --reload
