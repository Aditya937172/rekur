"""
Cron scheduler configuration for automated seasonal campaigns.
Uses APScheduler for reliable job scheduling.
"""

from datetime import datetime, timezone
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor

from app.core.config import load_settings

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the scheduler instance."""
    global _scheduler

    if _scheduler is None:
        settings = load_settings()

        jobstores = {"default": SQLAlchemyJobStore(url=settings.database_url)}

        executors = {"default": ThreadPoolExecutor(20)}

        job_defaults = {
            "coalesce": True,
            "max_instances": 3,
            "misfire_grace_time": 3600,
        }

        _scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone="UTC",
        )

    return _scheduler


async def start_scheduler():
    """Start the scheduler."""
    scheduler = get_scheduler()
    scheduler.start()
    logger.info("Scheduler started")

    add_seasonal_campaign_jobs(scheduler)
    logger.info("Seasonal campaign jobs scheduled")


def shutdown_scheduler():
    """Shutdown the scheduler gracefully."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=True)
        logger.info("Scheduler shutdown complete")
        _scheduler = None


def add_seasonal_campaign_jobs(scheduler: AsyncIOScheduler):
    """
    Add quarterly seasonal lookbook campaign jobs.

    Schedule:
    - Spring: March 1st week (Northern), September 1st week (Southern)
    - Summer: June 1st week (Northern), December 1st week (Southern)
    - Fall: September 1st week (Northern), March 1st week (Southern)
    - Winter: December 1st week (Northern), June 1st week (Southern)

    Each job runs daily at 10:00 AM UTC during the first week of the season.
    """

    # Northern Hemisphere campaigns
    scheduler.add_job(
        id="seasonal_spring_northern",
        func=run_seasonal_spring_campaign_northern,
        trigger=CronTrigger(
            month=3,
            day="1-7",
            hour=10,
            minute=0,
            timezone="UTC",
        ),
        replace_existing=True,
        name="Spring Lookbook Campaign (Northern Hemisphere)",
    )

    scheduler.add_job(
        id="seasonal_summer_northern",
        func=run_seasonal_summer_campaign_northern,
        trigger=CronTrigger(
            month=6,
            day="1-7",
            hour=10,
            minute=0,
            timezone="UTC",
        ),
        replace_existing=True,
        name="Summer Lookbook Campaign (Northern Hemisphere)",
    )

    scheduler.add_job(
        id="seasonal_fall_northern",
        func=run_seasonal_fall_campaign_northern,
        trigger=CronTrigger(
            month=9,
            day="1-7",
            hour=10,
            minute=0,
            timezone="UTC",
        ),
        replace_existing=True,
        name="Fall Lookbook Campaign (Northern Hemisphere)",
    )

    scheduler.add_job(
        id="seasonal_winter_northern",
        func=run_seasonal_winter_campaign_northern,
        trigger=CronTrigger(
            month=12,
            day="1-7",
            hour=10,
            minute=0,
            timezone="UTC",
        ),
        replace_existing=True,
        name="Winter Lookbook Campaign (Northern Hemisphere)",
    )

    # Southern Hemisphere campaigns
    scheduler.add_job(
        id="seasonal_spring_southern",
        func=run_seasonal_spring_campaign_southern,
        trigger=CronTrigger(
            month=9,
            day="1-7",
            hour=10,
            minute=0,
            timezone="UTC",
        ),
        replace_existing=True,
        name="Spring Lookbook Campaign (Southern Hemisphere)",
    )

    scheduler.add_job(
        id="seasonal_summer_southern",
        func=run_seasonal_summer_campaign_southern,
        trigger=CronTrigger(
            month=12,
            day="1-7",
            hour=10,
            minute=0,
            timezone="UTC",
        ),
        replace_existing=True,
        name="Summer Lookbook Campaign (Southern Hemisphere)",
    )

    scheduler.add_job(
        id="seasonal_fall_southern",
        func=run_seasonal_fall_campaign_southern,
        trigger=CronTrigger(
            month=3,
            day="1-7",
            hour=10,
            minute=0,
            timezone="UTC",
        ),
        replace_existing=True,
        name="Fall Lookbook Campaign (Southern Hemisphere)",
    )

    scheduler.add_job(
        id="seasonal_winter_southern",
        func=run_seasonal_winter_campaign_southern,
        trigger=CronTrigger(
            month=6,
            day="1-7",
            hour=10,
            minute=0,
            timezone="UTC",
        ),
        replace_existing=True,
        name="Winter Lookbook Campaign (Southern Hemisphere)",
    )

    logger.info("Added 8 seasonal campaign jobs (4 Northern + 4 Southern)")


async def run_seasonal_spring_campaign_northern():
    """Run spring campaign for Northern hemisphere customers."""
    from app.db.session import get_db
    from app.services.seasonal_scheduler import run_quarterly_seasonal_campaign
    from app.utils.season_utils import Hemisphere, Season

    logger.info("Running Spring campaign for Northern hemisphere")

    db = next(get_db())
    try:
        results = await run_quarterly_seasonal_campaign(
            db,
            store_id=1,
            hemisphere=Hemisphere.NORTHERN,
            limit=100,
        )
        logger.info(f"Spring campaign complete: {results}")
    finally:
        db.close()


async def run_seasonal_summer_campaign_northern():
    """Run summer campaign for Northern hemisphere customers."""
    from app.db.session import get_db
    from app.services.seasonal_scheduler import run_quarterly_seasonal_campaign
    from app.utils.season_utils import Hemisphere

    logger.info("Running Summer campaign for Northern hemisphere")

    db = next(get_db())
    try:
        results = await run_quarterly_seasonal_campaign(
            db,
            store_id=1,
            hemisphere=Hemisphere.NORTHERN,
            limit=100,
        )
        logger.info(f"Summer campaign complete: {results}")
    finally:
        db.close()


async def run_seasonal_fall_campaign_northern():
    """Run fall campaign for Northern hemisphere customers."""
    from app.db.session import get_db
    from app.services.seasonal_scheduler import run_quarterly_seasonal_campaign
    from app.utils.season_utils import Hemisphere

    logger.info("Running Fall campaign for Northern hemisphere")

    db = next(get_db())
    try:
        results = await run_quarterly_seasonal_campaign(
            db,
            store_id=1,
            hemisphere=Hemisphere.NORTHERN,
            limit=100,
        )
        logger.info(f"Fall campaign complete: {results}")
    finally:
        db.close()


async def run_seasonal_winter_campaign_northern():
    """Run winter campaign for Northern hemisphere customers."""
    from app.db.session import get_db
    from app.services.seasonal_scheduler import run_quarterly_seasonal_campaign
    from app.utils.season_utils import Hemisphere

    logger.info("Running Winter campaign for Northern hemisphere")

    db = next(get_db())
    try:
        results = await run_quarterly_seasonal_campaign(
            db,
            store_id=1,
            hemisphere=Hemisphere.NORTHERN,
            limit=100,
        )
        logger.info(f"Winter campaign complete: {results}")
    finally:
        db.close()


async def run_seasonal_spring_campaign_southern():
    """Run spring campaign for Southern hemisphere customers."""
    from app.db.session import get_db
    from app.services.seasonal_scheduler import run_quarterly_seasonal_campaign
    from app.utils.season_utils import Hemisphere

    logger.info("Running Spring campaign for Southern hemisphere")

    db = next(get_db())
    try:
        results = await run_quarterly_seasonal_campaign(
            db,
            store_id=1,
            hemisphere=Hemisphere.SOUTHERN,
            limit=100,
        )
        logger.info(f"Spring campaign complete: {results}")
    finally:
        db.close()


async def run_seasonal_summer_campaign_southern():
    """Run summer campaign for Southern hemisphere customers."""
    from app.db.session import get_db
    from app.services.seasonal_scheduler import run_quarterly_seasonal_campaign
    from app.utils.season_utils import Hemisphere

    logger.info("Running Summer campaign for Southern hemisphere")

    db = next(get_db())
    try:
        results = await run_quarterly_seasonal_campaign(
            db,
            store_id=1,
            hemisphere=Hemisphere.SOUTHERN,
            limit=100,
        )
        logger.info(f"Summer campaign complete: {results}")
    finally:
        db.close()


async def run_seasonal_fall_campaign_southern():
    """Run fall campaign for Southern hemisphere customers."""
    from app.db.session import get_db
    from app.services.seasonal_scheduler import run_quarterly_seasonal_campaign
    from app.utils.season_utils import Hemisphere

    logger.info("Running Fall campaign for Southern hemisphere")

    db = next(get_db())
    try:
        results = await run_quarterly_seasonal_campaign(
            db,
            store_id=1,
            hemisphere=Hemisphere.SOUTHERN,
            limit=100,
        )
        logger.info(f"Fall campaign complete: {results}")
    finally:
        db.close()


async def run_seasonal_winter_campaign_southern():
    """Run winter campaign for Southern hemisphere customers."""
    from app.db.session import get_db
    from app.services.seasonal_scheduler import run_quarterly_seasonal_campaign
    from app.utils.season_utils import Hemisphere

    logger.info("Running Winter campaign for Southern hemisphere")

    db = next(get_db())
    try:
        results = await run_quarterly_seasonal_campaign(
            db,
            store_id=1,
            hemisphere=Hemisphere.SOUTHERN,
            limit=100,
        )
        logger.info(f"Winter campaign complete: {results}")
    finally:
        db.close()


def schedule_test_campaign():
    """
    Schedule a test campaign to run immediately.
    Used for testing the pipeline.
    """
    scheduler = get_scheduler()

    scheduler.add_job(
        id="test_seasonal_campaign",
        func=run_seasonal_spring_campaign_northern,
        trigger=DateTrigger(run_date=datetime.now(timezone.utc)),
        replace_existing=True,
        name="Test Seasonal Campaign",
    )

    logger.info("Scheduled test campaign to run immediately")


def get_scheduled_jobs() -> list[dict]:
    """Get list of all scheduled jobs."""
    scheduler = get_scheduler()

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
        )

    return jobs


def remove_job(job_id: str) -> bool:
    """Remove a scheduled job."""
    scheduler = get_scheduler()

    try:
        scheduler.remove_job(job_id)
        logger.info(f"Removed job: {job_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to remove job {job_id}: {e}")
        return False
