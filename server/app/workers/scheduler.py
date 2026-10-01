"""Batch-scheduler worker.

Polls every settings.SCHEDULER_INTERVAL_SECONDS, runs the batch scheduler
for all subscribed customers in a worker thread (SQLAlchemy sync sessions
must not run on the event loop), then sleeps again.

Run with:  python -m app.workers.scheduler   (from server/)
"""

import asyncio

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.services.scheduler import run_scheduler_all

logger = get_logger("universal-downloader.workers.scheduler")


def _run_once() -> int:
    """One synchronous scheduling pass; returns the number of batches touched."""
    db: Session = SessionLocal()
    try:
        batches = run_scheduler_all(db)
        return len(batches)
    finally:
        db.close()


async def main() -> None:
    """Loop forever until cancelled."""
    configure_logging()
    logger.info(
        "scheduler.worker_started",
        interval_seconds=settings.SCHEDULER_INTERVAL_SECONDS,
    )
    try:
        while True:
            try:
                count = await asyncio.to_thread(_run_once)
                logger.info("scheduler.pass_complete", batches=count)
            except Exception:
                logger.exception("scheduler.pass_failed")
            await asyncio.sleep(settings.SCHEDULER_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("scheduler.worker_stopping")
        raise


if __name__ == "__main__":
    asyncio.run(main())
