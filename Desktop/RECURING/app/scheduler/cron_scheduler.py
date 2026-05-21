"""
Cron scheduler configuration for automated seasonal campaigns.
Uses APScheduler for reliable job scheduling.
Supports multi-store architecture.
"""

from datetime import datetime, timezone
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor

from app.core.config import load_settings

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler

    if _scheduler is None:
        settings = load_settings()

        jobstores = {"default": SQLAlchemyJobStore(url=settings.database_url)}

        executors = {"default": AsyncIOExecutor()}

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
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler started")
    else:
        logger.info("Scheduler already running")

    add_campaign_jobs(scheduler)
    logger.info("Campaign jobs scheduled")


def shutdown_scheduler():
    global _scheduler
    if _scheduler:
        try:
            if _scheduler.running:
                _scheduler.shutdown(wait=True)
                logger.info("Scheduler shutdown complete")
            else:
                logger.info("Scheduler was not running; shutdown skipped")
        except SchedulerNotRunningError:
            logger.info("Scheduler was not running; shutdown skipped")
        _scheduler = None


def add_campaign_jobs(scheduler: AsyncIOScheduler):
    """
    Add all campaign jobs for multi-store support.

    Seasonal schedule:
    - Spring: March 1-7 (Northern), September 1-7 (Southern)
    - Summer: June 1-7 (Northern), December 1-7 (Southern)
    - Fall: September 1-7 (Northern), March 1-7 (Southern)
    - Winter: December 1-7 (Northern), June 1-7 (Southern)

    Daily schedule:
    - Pre-churn: 08:00 UTC
    - Anniversary: 09:00 UTC
    - Silent customer: 10:00 UTC
    """

    # === SEASONAL CAMPAIGNS - Northern Hemisphere ===
    scheduler.add_job(
        id="seasonal_spring_northern",
        func=run_seasonal_spring_northern,
        trigger=CronTrigger(month=3, day="1-7", hour=10, minute=0, timezone="UTC"),
        replace_existing=True,
        name="Spring Campaign (Northern)",
    )

    scheduler.add_job(
        id="seasonal_summer_northern",
        func=run_seasonal_summer_northern,
        trigger=CronTrigger(month=6, day="1-7", hour=10, minute=0, timezone="UTC"),
        replace_existing=True,
        name="Summer Campaign (Northern)",
    )

    scheduler.add_job(
        id="seasonal_fall_northern",
        func=run_seasonal_fall_northern,
        trigger=CronTrigger(month=9, day="1-7", hour=10, minute=0, timezone="UTC"),
        replace_existing=True,
        name="Fall Campaign (Northern)",
    )

    scheduler.add_job(
        id="seasonal_winter_northern",
        func=run_seasonal_winter_northern,
        trigger=CronTrigger(month=12, day="1-7", hour=10, minute=0, timezone="UTC"),
        replace_existing=True,
        name="Winter Campaign (Northern)",
    )

    # === SEASONAL CAMPAIGNS - Southern Hemisphere ===
    scheduler.add_job(
        id="seasonal_spring_southern",
        func=run_seasonal_spring_southern,
        trigger=CronTrigger(month=9, day="1-7", hour=10, minute=0, timezone="UTC"),
        replace_existing=True,
        name="Spring Campaign (Southern)",
    )

    scheduler.add_job(
        id="seasonal_summer_southern",
        func=run_seasonal_summer_southern,
        trigger=CronTrigger(month=12, day="1-7", hour=10, minute=0, timezone="UTC"),
        replace_existing=True,
        name="Summer Campaign (Southern)",
    )

    scheduler.add_job(
        id="seasonal_fall_southern",
        func=run_seasonal_fall_southern,
        trigger=CronTrigger(month=3, day="1-7", hour=10, minute=0, timezone="UTC"),
        replace_existing=True,
        name="Fall Campaign (Southern)",
    )

    scheduler.add_job(
        id="seasonal_winter_southern",
        func=run_seasonal_winter_southern,
        trigger=CronTrigger(month=6, day="1-7", hour=10, minute=0, timezone="UTC"),
        replace_existing=True,
        name="Winter Campaign (Southern)",
    )

    # === DAILY CAMPAIGNS ===
    scheduler.add_job(
        id="daily_pre_churn_all_stores",
        func=run_pre_churn_all_stores,
        trigger=CronTrigger(hour=8, minute=0, timezone="UTC"),
        replace_existing=True,
        name="Daily Pre-Churn Campaign",
    )

    scheduler.add_job(
        id="daily_anniversary_all_stores",
        func=run_anniversary_all_stores,
        trigger=CronTrigger(hour=9, minute=0, timezone="UTC"),
        replace_existing=True,
        name="Daily Anniversary Campaign",
    )

    scheduler.add_job(
        id="daily_silent_customer_all_stores",
        func=run_silent_customer_all_stores,
        trigger=CronTrigger(hour=10, minute=0, timezone="UTC"),
        replace_existing=True,
        name="Daily Silent Customer Campaign",
    )

    scheduler.add_job(
        id="poll_gmail_replies",
        func=poll_gmail_replies_all_stores,
        trigger=CronTrigger(minute="*/5", timezone="UTC"),
        replace_existing=True,
        name="Poll Gmail for Customer Replies",
    )

    scheduler.add_job(
        id="nightly_shopify_sync_all_stores",
        func=nightly_shopify_sync_all_stores,
        trigger=CronTrigger(hour=2, minute=0, timezone="UTC"),
        replace_existing=True,
        name="Nightly Shopify Full Sync",
    )

    logger.info(
        "Added 13 campaign jobs (8 seasonal + 3 daily + 1 reply polling + 1 sync)"
    )


# === CAMPAIGN EXECUTION FUNCTIONS ===


async def run_seasonal_spring_northern():
    from app.services.campaign_orchestrator import run_seasonal_for_all_stores

    logger.info("Running Spring campaign (Northern) for all stores")
    results = await run_seasonal_for_all_stores("spring", "northern")
    logger.info(f"Spring Northern complete: {len(results)} stores")


async def run_seasonal_summer_northern():
    from app.services.campaign_orchestrator import run_seasonal_for_all_stores

    logger.info("Running Summer campaign (Northern) for all stores")
    results = await run_seasonal_for_all_stores("summer", "northern")
    logger.info(f"Summer Northern complete: {len(results)} stores")


async def run_seasonal_fall_northern():
    from app.services.campaign_orchestrator import run_seasonal_for_all_stores

    logger.info("Running Fall campaign (Northern) for all stores")
    results = await run_seasonal_for_all_stores("fall", "northern")
    logger.info(f"Fall Northern complete: {len(results)} stores")


async def run_seasonal_winter_northern():
    from app.services.campaign_orchestrator import run_seasonal_for_all_stores

    logger.info("Running Winter campaign (Northern) for all stores")
    results = await run_seasonal_for_all_stores("winter", "northern")
    logger.info(f"Winter Northern complete: {len(results)} stores")


async def run_seasonal_spring_southern():
    from app.services.campaign_orchestrator import run_seasonal_for_all_stores

    logger.info("Running Spring campaign (Southern) for all stores")
    results = await run_seasonal_for_all_stores("spring", "southern")
    logger.info(f"Spring Southern complete: {len(results)} stores")


async def run_seasonal_summer_southern():
    from app.services.campaign_orchestrator import run_seasonal_for_all_stores

    logger.info("Running Summer campaign (Southern) for all stores")
    results = await run_seasonal_for_all_stores("summer", "southern")
    logger.info(f"Summer Southern complete: {len(results)} stores")


async def run_seasonal_fall_southern():
    from app.services.campaign_orchestrator import run_seasonal_for_all_stores

    logger.info("Running Fall campaign (Southern) for all stores")
    results = await run_seasonal_for_all_stores("fall", "southern")
    logger.info(f"Fall Southern complete: {len(results)} stores")


async def run_seasonal_winter_southern():
    from app.services.campaign_orchestrator import run_seasonal_for_all_stores

    logger.info("Running Winter campaign (Southern) for all stores")
    results = await run_seasonal_for_all_stores("winter", "southern")
    logger.info(f"Winter Southern complete: {len(results)} stores")


async def run_pre_churn_all_stores():
    from app.services.campaign_orchestrator import run_pre_churn_for_all_stores

    logger.info("Running daily pre-churn for all stores")
    results = await run_pre_churn_for_all_stores()
    logger.info(f"Pre-churn complete: {len(results)} stores")


async def run_anniversary_all_stores():
    from app.services.campaign_orchestrator import run_anniversary_for_all_stores

    logger.info("Running daily anniversary for all stores")
    results = await run_anniversary_for_all_stores()
    logger.info(f"Anniversary complete: {len(results)} stores")


async def run_silent_customer_all_stores():
    from app.services.campaign_orchestrator import run_silent_customer_for_all_stores

    logger.info("Running daily silent customer for all stores")
    results = await run_silent_customer_for_all_stores()
    logger.info(f"Silent customer complete: {len(results)} stores")


async def poll_gmail_replies_all_stores():
    from app.services.campaign_orchestrator import poll_gmail_for_replies_all_stores

    logger.info("Polling Gmail for customer replies")
    results = await poll_gmail_for_replies_all_stores()
    logger.info(f"Reply polling complete: {results}")


async def nightly_shopify_sync_all_stores():
    from app.services.campaign_orchestrator import nightly_shopify_sync_all_stores

    logger.info("Running nightly Shopify sync for all stores")
    results = await nightly_shopify_sync_all_stores()
    logger.info(f"Nightly sync complete: {len(results)} stores")


def schedule_test_campaign():
    scheduler = get_scheduler()

    scheduler.add_job(
        id="test_campaign_all_stores",
        func=run_seasonal_spring_northern,
        trigger=DateTrigger(run_date=datetime.now(timezone.utc)),
        replace_existing=True,
        name="Test Campaign All Stores",
    )

    logger.info("Scheduled test campaign to run immediately")


def get_scheduled_jobs() -> list[dict]:
    scheduler = get_scheduler()
    if not scheduler.get_jobs():
        add_campaign_jobs(scheduler)

    jobs = []
    for job in scheduler.get_jobs():
        next_run_time = getattr(job, "next_run_time", None)
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "next_run": str(next_run_time) if next_run_time else None,
                "trigger": str(job.trigger),
            }
        )

    return jobs


def remove_job(job_id: str) -> bool:
    scheduler = get_scheduler()

    try:
        scheduler.remove_job(job_id)
        logger.info(f"Removed job: {job_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to remove job {job_id}: {e}")
        return False
