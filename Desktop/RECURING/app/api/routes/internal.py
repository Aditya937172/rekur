from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import require_internal_admin


router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_admin)],
)


@router.get("/scheduler/jobs")
async def internal_scheduler_jobs() -> list[dict]:
    from app.scheduler.cron_scheduler import get_scheduled_jobs

    return get_scheduled_jobs()


@router.post("/scheduler/jobs/{job_name}/run")
async def run_internal_scheduler_job(
    job_name: str,
    limit: int = Query(default=25, ge=1, le=500),
    season: str = Query(default="spring"),
    hemisphere: str = Query(default="northern"),
) -> dict:
    from app.services.campaign_orchestrator import (
        nightly_shopify_sync_all_stores,
        poll_gmail_for_replies_all_stores,
        run_anniversary_for_all_stores,
        run_pre_churn_for_all_stores,
        run_seasonal_for_all_stores,
        run_seasonal_lookbook_for_all_stores,
        run_silent_customer_for_all_stores,
    )

    if job_name == "pre-churn":
        return await run_pre_churn_for_all_stores(limit=limit)
    if job_name == "silent-customers":
        return await run_silent_customer_for_all_stores(limit=limit)
    if job_name == "anniversary":
        return await run_anniversary_for_all_stores(limit=limit)
    if job_name == "seasonal-lookbook":
        return await run_seasonal_lookbook_for_all_stores(season=season, limit=limit)
    if job_name == "seasonal":
        return await run_seasonal_for_all_stores(
            season=season,
            hemisphere=hemisphere,
            limit=limit,
        )
    if job_name == "gmail-replies":
        return await poll_gmail_for_replies_all_stores()
    if job_name == "nightly-sync":
        return await nightly_shopify_sync_all_stores()
    raise HTTPException(
        status_code=404,
        detail=(
            "Unknown internal job. Use pre-churn, silent-customers, anniversary, "
            "seasonal-lookbook, seasonal, gmail-replies, or nightly-sync."
        ),
    )


@router.post("/scheduler/test-campaign")
async def schedule_internal_test_campaign() -> dict:
    from app.scheduler.cron_scheduler import schedule_test_campaign

    schedule_test_campaign()
    return {"status": "scheduled", "job_id": "test_campaign_all_stores"}
