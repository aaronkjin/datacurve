"""Celery application — QA worker entrypoint."""

import os

from celery import Celery

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "datacurve_worker",
    broker=redis_url,
    backend=redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Task modules will be autodiscovered here:
# celery_app.autodiscover_tasks(["worker.tasks"])
