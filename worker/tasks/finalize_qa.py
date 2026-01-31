"""Mark trace as complete after QA pipeline for Celery task qa.finalize_qa"""

from __future__ import annotations

import logging

from core.models import QA
from db.models import TraceRow
from db.session import get_sync_session
from worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="qa.finalize_qa", bind=True, max_retries=0)
def finalize_qa(self, trace_id: str) -> dict:
    return _finalize_qa_impl(trace_id)


def _finalize_qa_impl(trace_id: str) -> dict:
    with get_sync_session() as session:
        row = session.query(TraceRow).filter_by(trace_id=trace_id).first()
        if row is None:
            raise ValueError(f"Trace not found: {trace_id}")

        if not row.qa_json:
            raise ValueError(f"Trace {trace_id} has no QA data")

        qa = QA.model_validate(row.qa_json)

        if qa.tests is None:
            raise ValueError(f"Trace {trace_id} missing qa.tests")
        if qa.judge is None:
            raise ValueError(f"Trace {trace_id} missing qa.judge")

        row.status = "complete"

    logger.info("Trace %s QA finalized — status set to complete", trace_id)
    return {"trace_id": trace_id, "status": "complete"}
