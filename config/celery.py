import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("smart_wifi_pass")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["operations"], related_name="celery_tasks")
app.conf.beat_schedule = {
    "smartpass-minutely": {
        "task": "operations.run_minutely_jobs",
        "schedule": 60.0,
    },
    "smartpass-hourly": {
        "task": "operations.run_hourly_jobs",
        "schedule": 3600.0,
    },
    "smartpass-daily": {
        "task": "operations.run_daily_jobs",
        "schedule": 86400.0,
    },
}
