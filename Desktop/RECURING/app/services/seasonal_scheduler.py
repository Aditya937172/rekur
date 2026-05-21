"""
Scheduler for quarterly seasonal lookbook campaigns.
Handles cron jobs, regional timing, and customer segmentation.
"""

from datetime import date, datetime, timedelta
from typing import Optional
import logging

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.models import BuyerMemory, Customer, Store
from app.services.send_policy_service import already_sent_seasonal
from app.utils.season_utils import (
    Hemisphere,
    Season,
    get_hemisphere,
    get_current_season,
    get_next_season,
    get_quarterly_campaign_week,
    is_optimal_send_time,
    season_to_display_name,
)

logger = logging.getLogger(__name__)


class SeasonalSchedulerError(RuntimeError):
    pass


def get_eligible_customers_for_seasonal_lookbook(
    db: Session,
    store_id: int,
    *,
    min_orders: int = 3,
    hemisphere: Optional[Hemisphere] = None,
    season: Optional[Season] = None,
    limit: Optional[int] = None,
) -> list[Customer]:
    """
    Get customers eligible for seasonal lookbook campaign.

    Criteria:
    - Has 3+ orders (active customer)
    - Has wardrobe items in memory
    - Hasn't received this season's lookbook yet
    - Optionally filtered by hemisphere
    """
    current_season = season or get_current_season(
        hemisphere=hemisphere or Hemisphere.NORTHERN
    )
    current_year = date.today().year

    query = (
        select(Customer)
        .join(BuyerMemory, BuyerMemory.customer_id == Customer.id)
        .where(
            Customer.store_id == store_id,
            Customer.total_orders >= min_orders,
            BuyerMemory.total_orders >= min_orders,
        )
    )

    if hemisphere:
        query = query.where(Customer.country.isnot(None))

    if limit:
        query = query.limit(limit)

    customers = db.scalars(query).all()

    eligible = []
    for customer in customers:
        if already_received_this_season(
            db, store_id, customer.id, current_season, current_year
        ):
            continue

        if hemisphere:
            customer_hemisphere = get_hemisphere(customer.country)
            if customer_hemisphere != hemisphere:
                continue

        eligible.append(customer)

    return eligible


def already_received_this_season(
    db: Session,
    store_id: int,
    customer_id: int,
    season: Season,
    year: int,
) -> bool:
    """Check RetentionSendLog for this season's sent lookbook."""
    return already_sent_seasonal(
        db,
        store_id=store_id,
        customer_id=customer_id,
        season_key=season.value,
        year=year,
    )


def get_campaign_schedule_for_year(
    year: int,
    hemisphere: Hemisphere = Hemisphere.NORTHERN,
) -> list[dict]:
    """
    Get all campaign dates for a year.
    Returns list of {season, start_date, end_date, hemisphere}.
    """
    seasons = [Season.SPRING, Season.SUMMER, Season.FALL, Season.WINTER]

    schedule = []
    for season in seasons:
        start_date, end_date = get_quarterly_campaign_week(year, season, hemisphere)
        schedule.append(
            {
                "season": season,
                "season_name": season_to_display_name(season),
                "campaign_start": start_date,
                "campaign_end": end_date,
                "hemisphere": hemisphere.value,
            }
        )

    return schedule


def should_run_campaign_today(
    hemisphere: Hemisphere = Hemisphere.NORTHERN,
) -> tuple[bool, Optional[Season]]:
    """
    Check if today falls within a campaign week.
    Returns (should_run, season) tuple.
    """
    today = date.today()
    year = today.year

    for season in [Season.SPRING, Season.SUMMER, Season.FALL, Season.WINTER]:
        start, end = get_quarterly_campaign_week(year, season, hemisphere)
        if start <= today <= end:
            return (True, season)

    return (False, None)


def get_upcoming_campaign(
    hemisphere: Hemisphere = Hemisphere.NORTHERN,
) -> Optional[dict]:
    """
    Get the next upcoming campaign within 30 days.
    """
    today = date.today()

    for season in [Season.SPRING, Season.SUMMER, Season.FALL, Season.WINTER]:
        start, end = get_quarterly_campaign_week(today.year, season, hemisphere)

        if start > today and (start - today).days <= 30:
            return {
                "season": season,
                "season_name": season_to_display_name(season),
                "start_date": start,
                "end_date": end,
                "days_until": (start - today).days,
            }

    return None


def get_customers_by_send_time(
    db: Session,
    store_id: int,
    target_hour: int = 10,
    hour_window: int = 2,
) -> list[Customer]:
    """
    Get customers whose local time is within optimal send window.
    Groups by timezone for batch sending.
    """
    eligible = get_eligible_customers_for_seasonal_lookbook(db, store_id)

    ready_to_send = []
    for customer in eligible:
        customer_hemisphere = get_hemisphere(customer.country)

        if is_optimal_send_time(
            customer_timezone=None,
            optimal_hour=target_hour,
            hour_window=hour_window,
        ):
            ready_to_send.append(customer)

    return ready_to_send


def batch_customers_by_hemisphere(
    customers: list[Customer],
) -> dict[Hemisphere, list[Customer]]:
    """Group customers by hemisphere for regional campaigns."""
    batches = {
        Hemisphere.NORTHERN: [],
        Hemisphere.SOUTHERN: [],
        Hemisphere.EQUATORIAL: [],
    }

    for customer in customers:
        hemisphere = get_hemisphere(customer.country)
        batches[hemisphere].append(customer)

    return {k: v for k, v in batches.items() if v}


async def run_quarterly_seasonal_campaign(
    db: Session,
    store_id: int,
    *,
    hemisphere: Optional[Hemisphere] = None,
    season: Optional[Season] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> dict:
    """
    Main function to run seasonal lookbook campaign.

    This is called by the cron scheduler quarterly.

    Returns:
        {
            "processed": int,
            "generated": int,
            "sent": int,
            "skipped": list,
            "by_hemisphere": dict,
        }
    """
    from app.services.seasonal_lookbook_service import (
        generate_seasonal_lookbook_for_customer,
    )

    if season:
        current_season = season
    elif hemisphere:
        current_season = get_current_season(hemisphere=hemisphere)
    else:
        should_run, current_season = should_run_campaign_today()
        if not should_run or not current_season:
            logger.info("No seasonal campaign scheduled for today")
            return {"processed": 0, "generated": 0, "sent": 0, "skipped": []}

    logger.info(
        f"Running {current_season.value} lookbook campaign for store {store_id}"
    )

    eligible = get_eligible_customers_for_seasonal_lookbook(
        db,
        store_id,
        min_orders=3,
        hemisphere=hemisphere,
        season=current_season,
        limit=limit,
    )

    logger.info(f"Found {len(eligible)} eligible customers")

    results = {
        "processed": 0,
        "generated": 0,
        "sent": 0,
        "skipped": [],
        "by_hemisphere": {
            Hemisphere.NORTHERN.value: {"processed": 0, "sent": 0},
            Hemisphere.SOUTHERN.value: {"processed": 0, "sent": 0},
            Hemisphere.EQUATORIAL.value: {"processed": 0, "sent": 0},
        },
    }

    for customer in eligible:
        results["processed"] += 1

        customer_hemisphere = get_hemisphere(customer.country)
        customer_season = current_season

        try:
            if dry_run:
                logger.info(
                    f"[DRY RUN] Would generate lookbook for customer {customer.id}"
                )
                results["generated"] += 1
                continue

            outfit = await generate_seasonal_lookbook_for_customer(
                db,
                store_id=store_id,
                customer_id=customer.id,
                season=customer_season,
                hemisphere=customer_hemisphere,
            )

            results["generated"] += 1
            results["by_hemisphere"][customer_hemisphere.value]["processed"] += 1

            if outfit and outfit.status == "sent":
                results["sent"] += 1
                results["by_hemisphere"][customer_hemisphere.value]["sent"] += 1

        except Exception as e:
            logger.error(f"Failed to generate lookbook for customer {customer.id}: {e}")
            results["skipped"].append(
                {
                    "customer_id": customer.id,
                    "reason": str(e),
                }
            )

    return results
