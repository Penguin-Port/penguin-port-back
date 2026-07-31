from celery import shared_task

from operations.tasks import run_daily_jobs, run_hourly_jobs, run_minutely_jobs


@shared_task(name="operations.run_minutely_jobs")
def minutely_task():
    return run_minutely_jobs()


@shared_task(name="operations.run_hourly_jobs")
def hourly_task():
    return run_hourly_jobs()


@shared_task(name="operations.run_daily_jobs")
def daily_task():
    return run_daily_jobs()
