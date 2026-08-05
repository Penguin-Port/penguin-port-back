"""Celery application for the FastAPI runtime.

The import is intentionally isolated from ``app.main`` so local MVP runs do
not require Redis/Celery unless ``USE_CELERY=1`` or a worker command is used.
"""

from app.config import settings

try:
    from celery import Celery
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal installs
    celery_app = None
else:
    celery_app = Celery(
        "smart_wifi_pass_fastapi",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
    )
    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        beat_schedule={
            "smartpass-fastapi-minutely": {
                "task": "app.tasks.run_minutely_jobs",
                "schedule": 60.0,
            },
            "smartpass-fastapi-hourly": {
                "task": "app.tasks.run_hourly_jobs",
                "schedule": 3600.0,
            },
            "smartpass-fastapi-daily": {
                "task": "app.tasks.run_daily_jobs",
                "schedule": 86400.0,
            },
        },
    )
    celery_app.autodiscover_tasks(["app"])

