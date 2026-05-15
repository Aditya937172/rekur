from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.config import AppSettings, load_settings
from app.models import (
    BuyerMemory,
    Customer,
    EmailEngagement,
    Event,
    Order,
    Product,
    RetentionCampaignState,
    RetentionSendLog,
    Store,
    TrackingSession,
)
from app.schemas import CampaignRunRequest, CampaignRunResponse, ChurnRiskResponse, SilentCustomerResponse
from app.services.buyer_memory_service import get_buyer_memory, update_buyer_memory_for_customer
from app.services.message_engine import MessageEngineError, call_groq
from app.services.outfit_service import (
    OutfitServiceError,
    generate_custom_outfit_for_customer,
    product_to_context,
)
from app.services.recommendation_engine import get_recommendations_for_customer
from app.services.send_policy_service import SendPolicyError, enforce_send_policy


class RetentionCampaignServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_seasonal_lookbook_campaign(
    db: Session,
    store_id: int,
    request: CampaignRunRequest,
    *,
    season: str | None = None,
    settings: AppSettings | None = None,
) -> CampaignRunResponse:
    settings = settings or load_settings()
    store = ensure_store(db, store_id)
    season_name = season or current_season()
    rows = eligible_customers(db, store_id, request, min_orders=3)
    outfits = []
    skipped: list[dict[str, Any]] = []
    sent = 0
    processed = 0
    for customer in rows[: request.limit]:
        processed += 1
        try:
            enforce_send_policy(
                db,
                store_id=store_id,
                customer_id=customer.id,
                campaign_type="seasonal_lookbook",
                force=request.force or bool(request.recipient_email),
            )
            memory = update_buyer_memory_for_customer(db, store_id, customer.id)
            products = owned_products_for_memory(db, memory, limit=4)
            if len(products) < 2:
                skipped.append({"customer_id": customer.id, "reason": "not enough owned pieces"})
                continue
            product_context = [product_to_context(store, product, "owned") for product in products]
            subject = f"found a {season_name} outfit hiding in your wardrobe"
            body = generate_campaign_copy(
                settings=settings,
                fallback=seasonal_fallback_body(customer, memory, season_name),
                prompt=(
                    "Write a seasonal lookbook email for a clothing brand customer.\n"
                    "They already own all the pieces shown.\n"
                    "This is not about buying anything new.\n"
                    "Frame it as discovering outfits they already have.\n"
                    f"Incoming season: {season_name}\n"
                    f"Customer style profile: {memory.memory_summary}\n"
                    "Acknowledge their specific style aesthetic by name.\n"
                    "Sound like a friend who found great combinations in their wardrobe.\n"
                    "GenZ tone. Max 5 sentences introduction.\n"
                    "No selling. No product links. Pure styling value.\n"
                    f"One subtle final line only: missing something for {season_name}? we might have it."
                ),
            )
            prompt = (
                "One single seasonal fashion lookbook image, exactly 3 styling options in one cohesive triptych. "
                f"Use only these already-owned wardrobe pieces: {', '.join(product.title for product in products)}. "
                f"Season: {season_name}. No text, no logos, no labels, no watermarks. "
                "Realistic premium D2C campaign, natural attractive models."
            )
            outfit = generate_custom_outfit_for_customer(
                db,
                store_id=store_id,
                customer_id=customer.id,
                order_id=None,
                product_context=product_context,
                trigger_reason="seasonal_lookbook",
                prompt=prompt,
                email_subject=subject,
                email_body=body,
                send_email=request.send_email,
                recipient_email=request.recipient_email,
                settings=settings,
            )
            outfits.append(outfit)
            if outfit.status == "sent":
                sent += 1
        except (OutfitServiceError, SendPolicyError) as exc:
            skipped.append({"customer_id": customer.id, "reason": str(exc)})
    return CampaignRunResponse(
        store_id=store_id,
        campaign_type="seasonal_lookbook",
        processed=processed,
        generated=len(outfits),
        sent=sent,
        skipped=skipped,
        outfits=outfits,
    )


def compute_churn_risk(
    db: Session,
    store_id: int,
    *,
    limit: int = 100,
) -> list[ChurnRiskResponse]:
    ensure_store(db, store_id)
    customers = (
        db.query(Customer)
        .filter(Customer.store_id == store_id, Customer.total_orders > 0)
        .limit(limit)
        .all()
    )
    rows = []
    for customer in customers:
        score, signals = churn_score_for_customer(db, store_id, customer)
        stage = churn_stage(score)
        upsert_campaign_state(
            db,
            store_id=store_id,
            customer_id=customer.id,
            campaign_type="pre_churn",
            stage=stage,
            status="active" if score >= 65 else "monitor",
            score=score,
            metadata=signals,
        )
        rows.append(
            ChurnRiskResponse(
                customer_id=customer.id,
                customer_name=display_name(customer),
                score=round(score, 2),
                stage=stage,
                signals=signals,
            )
        )
    db.commit()
    return sorted(rows, key=lambda item: item.score, reverse=True)


def run_pre_churn_campaign(
    db: Session,
    store_id: int,
    request: CampaignRunRequest,
    *,
    settings: AppSettings | None = None,
) -> CampaignRunResponse:
    settings = settings or load_settings()
    store = ensure_store(db, store_id)
    risks = compute_churn_risk(db, store_id, limit=max(request.limit * 3, 50))
    skipped: list[dict[str, Any]] = []
    outfits = []
    sent = 0
    processed = 0
    for risk in risks:
        if processed >= request.limit:
            break
        if request.customer_id and risk.customer_id != request.customer_id:
            continue
        if risk.score < 65 and not request.force:
            continue
        processed += 1
        customer = db.get(Customer, risk.customer_id)
        if not customer:
            continue
        try:
            enforce_send_policy(
                db,
                store_id=store_id,
                customer_id=customer.id,
                campaign_type="pre_churn",
                force=request.force or bool(request.recipient_email),
            )
            memory = get_buyer_memory(db, store_id, customer.id)
            product = best_matching_new_product(db, store, memory)
            if not product:
                skipped.append({"customer_id": customer.id, "reason": "no matching product"})
                continue
            body, trigger_reason = pre_churn_copy_and_trigger(
                settings=settings,
                customer=customer,
                memory=memory,
                product=product,
                score=risk.score,
            )
            product_context = [product_to_context(store, product, "recommended_pairing")]
            outfit = generate_custom_outfit_for_customer(
                db,
                store_id=store_id,
                customer_id=customer.id,
                order_id=None,
                product_context=product_context,
                trigger_reason=trigger_reason,
                prompt=(
                    "One beautiful fashion product styling image. Exactly one cohesive visual, no text. "
                    f"Hero product: {product.title}. Match this style profile: {memory.memory_summary}. "
                    "Premium D2C clothing campaign, natural model, no logos, no labels."
                ),
                email_subject="saw this and thought of you",
                email_body=body,
                send_email=request.send_email,
                recipient_email=request.recipient_email,
                settings=settings,
            )
            outfits.append(outfit)
            if outfit.status == "sent":
                sent += 1
        except (OutfitServiceError, SendPolicyError) as exc:
            skipped.append({"customer_id": customer.id, "reason": str(exc)})
    return CampaignRunResponse(
        store_id=store_id,
        campaign_type="pre_churn",
        processed=processed,
        generated=len(outfits),
        sent=sent,
        skipped=skipped,
        outfits=outfits,
    )


def detect_silent_customers(
    db: Session,
    store_id: int,
    *,
    limit: int = 100,
) -> list[SilentCustomerResponse]:
    ensure_store(db, store_id)
    cutoff = utc_now() - timedelta(days=60)
    customers = db.query(Customer).filter(Customer.store_id == store_id).limit(limit * 5).all()
    rows: list[SilentCustomerResponse] = []
    for customer in customers:
        if customer.last_order_date and days_since(customer.last_order_date) < 60:
            continue
        sent_count, open_count, click_count = engagement_counts(db, store_id, customer.id, cutoff)
        if sent_count < 3:
            continue
        open_rate = open_count / sent_count if sent_count else 0
        click_rate = click_count / sent_count if sent_count else 0
        if open_rate > 0.40 and click_rate < 0.05 and not active_pre_churn(db, store_id, customer.id):
            rows.append(
                SilentCustomerResponse(
                    customer_id=customer.id,
                    customer_name=display_name(customer),
                    last_purchase_days=days_since(customer.last_order_date),
                    open_rate_60d=round(open_rate, 4),
                    click_rate_60d=round(click_rate, 4),
                    emails_sent_60d=sent_count,
                )
            )
        if len(rows) >= limit:
            break
    return rows


def run_silent_customer_campaign(
    db: Session,
    store_id: int,
    request: CampaignRunRequest,
    *,
    settings: AppSettings | None = None,
) -> CampaignRunResponse:
    settings = settings or load_settings()
    store = ensure_store(db, store_id)
    silent_rows = detect_silent_customers(db, store_id, limit=max(request.limit * 3, 50))
    skipped: list[dict[str, Any]] = []
    outfits = []
    sent = 0
    processed = 0
    for row in silent_rows:
        if processed >= request.limit:
            break
        if request.customer_id and row.customer_id != request.customer_id:
            continue
        processed += 1
        customer = db.get(Customer, row.customer_id)
        if not customer:
            continue
        try:
            enforce_send_policy(
                db,
                store_id=store_id,
                customer_id=customer.id,
                campaign_type="silent_customer",
                force=request.force or bool(request.recipient_email),
            )
            memory = get_buyer_memory(db, store_id, customer.id)
            recommendation = get_recommendations_for_customer(db, store_id, customer.id, product_limit=1)
            if recommendation.recommendations:
                product = db.get(Product, recommendation.recommendations[0].product_id)
            else:
                product = best_matching_new_product(db, store, memory)
            if not product:
                skipped.append({"customer_id": customer.id, "reason": "no product recommendation"})
                continue
            body = generate_campaign_copy(
                settings=settings,
                fallback=(
                    f"okay {customer.first_name or 'there'}, this one feels very you based on what you usually go for. "
                    f"i’d try the {product.title} with your existing pieces first. "
                    "what kind of fit are you actually looking for right now?"
                ),
                prompt=(
                    "Write a message for a customer who keeps opening our emails but never buys.\n"
                    "Do not acknowledge this pattern directly.\n"
                    f"Product suggestion: {product.title}\n"
                    f"Profile summary: {memory.memory_summary}\n"
                    "Use celebrity style reference if their vibe matches a known aesthetic.\n"
                    "End with one genuine question about what they are looking for.\n"
                    "Invite a reply naturally. GenZ casual tone. Max 4 sentences.\n"
                    "Make them feel genuinely understood not targeted."
                ),
            )
            outfit = generate_custom_outfit_for_customer(
                db,
                store_id=store_id,
                customer_id=customer.id,
                order_id=None,
                product_context=[product_to_context(store, product, "recommended_pairing")],
                trigger_reason="silent_customer",
                prompt=(
                    "One polished fashion image, one cohesive visual, no text. "
                    f"Hero product: {product.title}. Match the customer's wardrobe memory: {memory.memory_summary}. "
                    "Natural model, premium D2C styling, no logos or labels."
                ),
                email_subject="this felt like your vibe",
                email_body=body,
                send_email=request.send_email,
                recipient_email=request.recipient_email,
                settings=settings,
            )
            outfits.append(outfit)
            if outfit.status == "sent":
                sent += 1
        except Exception as exc:
            skipped.append({"customer_id": customer.id, "reason": str(exc)})
    return CampaignRunResponse(
        store_id=store_id,
        campaign_type="silent_customer",
        processed=processed,
        generated=len(outfits),
        sent=sent,
        skipped=skipped,
        outfits=outfits,
    )


def churn_score_for_customer(db: Session, store_id: int, customer: Customer) -> tuple[float, dict[str, Any]]:
    now = utc_now()
    orders = db.scalars(
        select(Order)
        .where(Order.store_id == store_id, Order.customer_id == customer.id)
        .order_by(Order.created_at.asc())
    ).all()
    gaps = [
        max((orders[index].created_at - orders[index - 1].created_at).days, 1)
        for index in range(1, len(orders))
        if orders[index].created_at and orders[index - 1].created_at
    ]
    personal_purchase_avg = mean(gaps) if gaps else 90
    current_gap = days_since(customer.last_order_date) or 0
    purchase_drop = current_gap >= personal_purchase_avg * 1.4
    purchase_score = 35 if purchase_drop else 0

    six_months = now - timedelta(days=180)
    sent_6m, opens_6m, clicks_6m = engagement_counts(db, store_id, customer.id, six_months)
    personal_open_rate = opens_6m / sent_6m if sent_6m else 0
    last_sent, last_opens, _ = last_email_engagement(db, store_id, customer.id, limit=3)
    recent_open_rate = last_opens / last_sent if last_sent else personal_open_rate
    email_drop = personal_open_rate > 0 and recent_open_rate <= personal_open_rate * 0.5
    email_score = 30 if email_drop else 0

    visit_gaps = visit_gap_days(db, store_id, customer.id)
    personal_visit_avg = mean(visit_gaps[:-1]) if len(visit_gaps) > 1 else 30
    current_visit_gap = visit_gaps[-1] if visit_gaps else 999
    visit_drop = current_visit_gap >= personal_visit_avg * 2
    visit_score = 20 if visit_drop else 0

    depth_drop = engagement_depth_drop(db, store_id, customer.id)
    depth_score = 15 if depth_drop else 0
    score = purchase_score + email_score + visit_score + depth_score
    return score, {
        "purchase_frequency_drop": purchase_drop,
        "personal_avg_purchase_days": personal_purchase_avg,
        "current_purchase_gap_days": current_gap,
        "email_engagement_drop": email_drop,
        "personal_open_rate_6m": personal_open_rate,
        "recent_open_rate_last_3": recent_open_rate,
        "site_visit_frequency_drop": visit_drop,
        "personal_avg_visit_gap_days": personal_visit_avg,
        "current_visit_gap_days": current_visit_gap,
        "engagement_depth_drop": depth_drop,
    }


def churn_stage(score: float) -> str:
    if score >= 80:
        return "stage_2_critical"
    if score >= 65:
        return "stage_1_pre_churn"
    if score >= 40:
        return "monitor"
    return "healthy"


def engagement_counts(
    db: Session,
    store_id: int,
    customer_id: int,
    cutoff: datetime,
) -> tuple[int, int, int]:
    rows = db.execute(
        select(
            EmailEngagement.event_type,
            func.count(EmailEngagement.id),
        )
        .where(
            EmailEngagement.store_id == store_id,
            EmailEngagement.customer_id == customer_id,
            EmailEngagement.timestamp >= cutoff,
        )
        .group_by(EmailEngagement.event_type)
    ).all()
    counts = {event_type: int(count or 0) for event_type, count in rows}
    return counts.get("sent", 0), counts.get("open", 0), counts.get("click", 0)


def last_email_engagement(
    db: Session,
    store_id: int,
    customer_id: int,
    *,
    limit: int,
) -> tuple[int, int, int]:
    sent_rows = db.scalars(
        select(EmailEngagement)
        .where(
            EmailEngagement.store_id == store_id,
            EmailEngagement.customer_id == customer_id,
            EmailEngagement.event_type == "sent",
        )
        .order_by(EmailEngagement.timestamp.desc())
        .limit(limit)
    ).all()
    if not sent_rows:
        return 0, 0, 0
    since = min(row.timestamp for row in sent_rows)
    return engagement_counts(db, store_id, customer_id, since)


def visit_gap_days(db: Session, store_id: int, customer_id: int) -> list[int]:
    sessions = db.scalars(
        select(TrackingSession)
        .where(
            TrackingSession.store_id == store_id,
            TrackingSession.customer_id == customer_id,
        )
        .order_by(TrackingSession.last_seen_at.asc())
    ).all()
    if not sessions:
        return []
    gaps = [
        max((sessions[index].last_seen_at - sessions[index - 1].last_seen_at).days, 1)
        for index in range(1, len(sessions))
        if sessions[index].last_seen_at and sessions[index - 1].last_seen_at
    ]
    current_gap = days_since(sessions[-1].last_seen_at)
    gaps.append(current_gap if current_gap is not None else 999)
    return gaps


def engagement_depth_drop(db: Session, store_id: int, customer_id: int) -> bool:
    rows = db.execute(
        select(
            TrackingSession.id,
            func.coalesce(func.sum(Event.time_spent), 0),
            func.sum(case((Event.event_type == "product_view", 1), else_=0)),
        )
        .join(Event, Event.session_id == TrackingSession.id)
        .where(
            TrackingSession.store_id == store_id,
            TrackingSession.customer_id == customer_id,
        )
        .group_by(TrackingSession.id)
        .order_by(func.max(Event.timestamp).asc())
    ).all()
    if len(rows) < 4:
        return False
    baseline = rows[:-3]
    recent = rows[-3:]
    baseline_depth = mean(float(time_spent or 0) + float(views or 0) * 10000 for _, time_spent, views in baseline)
    recent_depth = mean(float(time_spent or 0) + float(views or 0) * 10000 for _, time_spent, views in recent)
    return baseline_depth > 0 and recent_depth < baseline_depth * 0.5


def eligible_customers(
    db: Session,
    store_id: int,
    request: CampaignRunRequest,
    *,
    min_orders: int,
) -> list[Customer]:
    query = db.query(Customer).filter(
        Customer.store_id == store_id,
        Customer.total_orders >= min_orders,
    )
    if request.customer_id:
        query = query.filter(Customer.id == request.customer_id)
    return query.order_by(Customer.last_order_date.desc()).all()


def owned_products_for_memory(db: Session, memory: BuyerMemory, *, limit: int) -> list[Product]:
    product_ids = [
        int(item["product_id"])
        for item in (memory.wardrobe_items_json or [])
        if item.get("product_id")
    ][:limit]
    if not product_ids:
        return []
    products = db.scalars(select(Product).where(Product.id.in_(product_ids))).all()
    product_by_id = {product.id: product for product in products}
    return [product_by_id[product_id] for product_id in product_ids if product_id in product_by_id]


def best_matching_new_product(db: Session, store: Store, memory: BuyerMemory) -> Product | None:
    owned_ids = {
        int(item["product_id"])
        for item in (memory.wardrobe_items_json or [])
        if item.get("product_id")
    }
    products = db.scalars(
        select(Product)
        .where(Product.store_id == store.id, Product.id.not_in(owned_ids or {-1}))
        .order_by(Product.updated_at.desc())
        .limit(100)
    ).all()
    if not products:
        return None
    signals = f"{memory.favorite_categories or ''} {memory.favorite_colors or ''} {memory.style_tags or ''}".lower()
    scored = []
    for product in products:
        text = f"{product.title} {product.tags or ''}".lower()
        score = sum(1 for token in signals.split(",") if token.strip() and token.strip() in text)
        scored.append((score, product))
    scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    return scored[0][1]


def pre_churn_copy_and_trigger(
    *,
    settings: AppSettings,
    customer: Customer,
    memory: BuyerMemory,
    product: Product,
    score: float,
) -> tuple[str, str]:
    if score >= 80:
        return (
            generate_campaign_copy(
                settings=settings,
                fallback="no pressure. just leaving this here.",
                prompt=(
                    "Write a re-engagement message using minimal language.\n"
                    "Maximum 6 words of copy total.\n"
                    f"Product: {product.title}\n"
                    "The restraint IS the message.\n"
                    "Example tone: no pressure. just leaving this here.\n"
                    "No discount. No urgency. No explanation."
                ),
            ),
            "pre_churn_stage_2",
        )
    return (
        generate_campaign_copy(
            settings=settings,
            fallback=(
                f"saw the {product.title} and it felt very {memory.favorite_categories or 'you'}. "
                "would you style it clean or a little louder?"
            ),
            prompt=(
                "Write a casual style nudge message.\n"
                "Do not mention the customer has been away.\n"
                "Do not offer a discount.\n"
                f"Reference something new in store matching their aesthetic: {memory.memory_summary}.\n"
                f"Product: {product.title}\n"
                "Sound like a friend who thought of them when they saw something new.\n"
                "GenZ casual tone. Maximum 3 sentences. One product image.\n"
                "End with a casual non-pressuring question."
            ),
        ),
        "pre_churn_stage_1",
    )


def generate_campaign_copy(
    *,
    settings: AppSettings,
    fallback: str,
    prompt: str,
) -> str:
    try:
        return call_groq(settings=settings, prompt=prompt)
    except MessageEngineError:
        return fallback


def upsert_campaign_state(
    db: Session,
    *,
    store_id: int,
    customer_id: int,
    campaign_type: str,
    stage: str,
    status: str,
    score: float,
    metadata: dict[str, Any],
) -> RetentionCampaignState:
    state = db.scalar(
        select(RetentionCampaignState).where(
            RetentionCampaignState.store_id == store_id,
            RetentionCampaignState.customer_id == customer_id,
            RetentionCampaignState.campaign_type == campaign_type,
        )
    )
    if not state:
        state = RetentionCampaignState(
            store_id=store_id,
            customer_id=customer_id,
            campaign_type=campaign_type,
        )
        db.add(state)
    state.stage = stage
    state.status = status
    state.score = score
    state.metadata_json = metadata
    state.updated_at = utc_now()
    return state


def active_pre_churn(db: Session, store_id: int, customer_id: int) -> bool:
    return (
        db.scalar(
            select(RetentionCampaignState.id).where(
                RetentionCampaignState.store_id == store_id,
                RetentionCampaignState.customer_id == customer_id,
                RetentionCampaignState.campaign_type == "pre_churn",
                RetentionCampaignState.status == "active",
            )
        )
        is not None
    )


def seasonal_fallback_body(customer: Customer, memory: BuyerMemory, season: str) -> str:
    return (
        f"hey {customer.first_name or 'there'}, i found a {season} combo already sitting in your wardrobe. "
        f"it leans {memory.favorite_categories or 'very you'} with {memory.favorite_colors or 'your usual palette'}. "
        "not selling you anything, just showing the pieces in a new way. "
        f"missing something for {season}? we might have it."
    )


def current_season() -> str:
    month = utc_now().month
    if month in {3, 4, 5}:
        return "spring"
    if month in {6, 7, 8}:
        return "summer"
    if month in {9, 10, 11}:
        return "fall"
    return "winter"


def ensure_store(db: Session, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if not store:
        raise RetentionCampaignServiceError(f"Store {store_id} was not found.", status_code=404)
    return store


def display_name(customer: Customer) -> str:
    name = " ".join(
        part for part in [customer.first_name, customer.last_name] if part
    ).strip()
    return name or customer.email or f"Customer {customer.id}"


def days_since(value: datetime | None) -> int | None:
    if not value:
        return None
    now = utc_now()
    if value.tzinfo is None:
        now = now.replace(tzinfo=None)
    return max((now - value).days, 0)
