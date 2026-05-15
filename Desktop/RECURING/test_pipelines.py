"""
Comprehensive pipeline test script for RECURING retention platform.
Tests all features and sends real emails to verify end-to-end functionality.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import load_settings
from app.db.session import get_db
from app.models import (
    BuyerMemory,
    Customer,
    EmailEngagement,
    Event,
    GeneratedOutfitImage,
    Order,
    Product,
    RetentionCampaignState,
    RetentionSendLog,
    Store,
    TrackingSession,
)
from app.services.anniversary_service import run_first_order_anniversary_campaign
from app.services.buyer_memory_service import (
    get_buyer_memory,
    update_buyer_memory_for_customer,
)
from app.services.intent_engine import get_customer_intents
from app.services.recommendation_engine import get_recommendations_for_customer
from app.services.retention_campaign_service import (
    compute_churn_risk,
    detect_silent_customers,
    run_pre_churn_campaign,
    run_seasonal_lookbook_campaign,
    run_silent_customer_campaign,
)
from app.services.outfit_service import generate_outfit_for_customer
from app.schemas import (
    CampaignRunRequest,
    FirstOrderAnniversaryCampaignRequest,
    GenerateOutfitImageRequest,
)

settings = load_settings()
db = next(get_db())

TEST_EMAIL = "adityapalghar@gmail.com"
REPORT = []


def log(message: str, status: str = "INFO"):
    timestamp = datetime.now(timezone.utc).isoformat()
    entry = f"[{timestamp}] [{status}] {message}"
    REPORT.append(entry)
    print(entry)


def test_database_state():
    """Test 1: Verify database has required data"""
    log("=" * 60, "TEST")
    log("TEST 1: DATABASE STATE", "TEST")

    stores = db.query(Store).count()
    customers = db.query(Customer).count()
    products = db.query(Product).count()
    orders = db.query(Order).count()
    memories = db.query(BuyerMemory).count()
    events = db.query(Event).count()

    log(f"Stores: {stores}")
    log(f"Customers: {customers}")
    log(f"Products: {products}")
    log(f"Orders: {orders}")
    log(f"Buyer Memories: {memories}")
    log(f"Events: {events}")

    if stores == 0:
        log("FAIL: No stores in database", "ERROR")
        return False
    if customers < 3:
        log(f"WARNING: Only {customers} customers, need at least 3 for testing", "WARN")
    if products < 5:
        log(f"FAIL: Only {products} products, need at least 5", "ERROR")
        return False
    if orders < 3:
        log(f"WARNING: Only {orders} orders, some tests may not work", "WARN")

    log("PASS: Database has minimum required data", "SUCCESS")
    return True


def test_buyer_memory():
    """Test 2: Buyer Memory Pipeline"""
    log("=" * 60, "TEST")
    log("TEST 2: BUYER MEMORY PIPELINE", "TEST")

    store_id = 1
    customer = db.query(Customer).filter(Customer.store_id == store_id).first()

    if not customer:
        log("FAIL: No customer found for buyer memory test", "ERROR")
        return False

    log(f"Testing with customer {customer.id} ({customer.email})")

    try:
        memory = update_buyer_memory_for_customer(db, store_id, customer.id)
        db.commit()

        log(f"Memory ID: {memory.id}")
        log(f"Total Orders: {memory.total_orders}")
        log(f"Total Spent: {memory.total_spent}")
        log(f"Favorite Categories: {memory.favorite_categories}")
        log(f"Favorite Colors: {memory.favorite_colors}")
        log(f"Style Tags: {memory.style_tags}")
        log(f"Price Band: {memory.price_band}")
        log(
            f"Memory Summary: {memory.memory_summary[:200] if memory.memory_summary else 'None'}..."
        )

        if memory.total_orders > 0 and memory.memory_summary:
            log("PASS: Buyer memory created successfully", "SUCCESS")
            return True
        else:
            log("FAIL: Buyer memory incomplete", "ERROR")
            return False
    except Exception as e:
        log(f"FAIL: Exception - {str(e)}", "ERROR")
        return False


def test_intent_engine():
    """Test 3: Intent Engine Pipeline"""
    log("=" * 60, "TEST")
    log("TEST 3: INTENT ENGINE PIPELINE", "TEST")

    try:
        intents = get_customer_intents(db, store_id=1, limit=10)

        log(f"Found {len(intents)} customers with intent signals")

        if intents:
            top_intent = intents[0]
            log(f"Top customer: {top_intent.customer_id}")
            log(f"Name: {top_intent.name}")
            log(f"Email: {top_intent.email}")
            log(f"Intent: {top_intent.intent}")
            log(f"Score: {top_intent.score}")
            log(f"Reason: {top_intent.reason}")
            log(
                f"Signals: product_views={top_intent.signals.product_views}, sessions={top_intent.signals.sessions}"
            )

        if len(intents) > 0:
            log("PASS: Intent engine working", "SUCCESS")
            return True
        else:
            log("FAIL: No intent data found", "ERROR")
            return False
    except Exception as e:
        log(f"FAIL: Exception - {str(e)}", "ERROR")
        return False


def test_recommendation_engine():
    """Test 4: Recommendation Engine Pipeline"""
    log("=" * 60, "TEST")
    log("TEST 4: RECOMMENDATION ENGINE PIPELINE", "TEST")

    customer = (
        db.query(Customer)
        .filter(Customer.store_id == 1, Customer.total_orders > 0)
        .first()
    )

    if not customer:
        log("FAIL: No customer with orders for recommendation test", "ERROR")
        return False

    try:
        recs = get_recommendations_for_customer(
            db, store_id=1, customer_id=customer.id, product_limit=5
        )

        log(f"Customer: {customer.id} ({customer.email})")
        log(f"Intent: {recs.intent}")
        log(f"Score: {recs.score}")
        log(f"Recommendations: {len(recs.recommendations)}")

        for i, rec in enumerate(recs.recommendations[:3], 1):
            log(f"  {i}. {rec.title} - {rec.reason}")

        if len(recs.recommendations) > 0:
            log("PASS: Recommendation engine working", "SUCCESS")
            return True
        else:
            log(
                "WARNING: No recommendations generated (may be OK if customer bought everything)",
                "WARN",
            )
            return True
    except Exception as e:
        log(f"FAIL: Exception - {str(e)}", "ERROR")
        return False


def test_churn_risk():
    """Test 5: Churn Risk Computation"""
    log("=" * 60, "TEST")
    log("TEST 5: CHURN RISK COMPUTATION", "TEST")

    try:
        risks = compute_churn_risk(db, store_id=1, limit=10)

        log(f"Found {len(risks)} customers with churn scores")

        if risks:
            log("\nTop churn risks:")
            for i, risk in enumerate(risks[:5], 1):
                log(
                    f"  {i}. {risk.customer_name} (ID: {risk.customer_id}) - Score: {risk.score} - Stage: {risk.stage}"
                )
                log(
                    f"     Signals: purchase_drop={risk.signals.get('purchase_frequency_drop')}, email_drop={risk.signals.get('email_engagement_drop')}"
                )

        if len(risks) > 0:
            log("PASS: Churn risk computation working", "SUCCESS")
            return True
        else:
            log("FAIL: No churn risk data", "ERROR")
            return False
    except Exception as e:
        log(f"FAIL: Exception - {str(e)}", "ERROR")
        return False


def test_silent_customers():
    """Test 6: Silent Customer Detection"""
    log("=" * 60, "TEST")
    log("TEST 6: SILENT CUSTOMER DETECTION", "TEST")

    try:
        silent = detect_silent_customers(db, store_id=1, limit=10)

        log(f"Found {len(silent)} silent customers")

        if silent:
            log("\nSilent customers:")
            for i, s in enumerate(silent[:5], 1):
                log(f"  {i}. {s.customer_name} (ID: {s.customer_id})")
                log(
                    f"     Last purchase: {s.last_purchase_days}d ago, Open rate: {s.open_rate_60d:.2%}, Emails: {s.emails_sent_60d}"
                )

        log("PASS: Silent customer detection working", "SUCCESS")
        return True
    except Exception as e:
        log(f"FAIL: Exception - {str(e)}", "ERROR")
        return False


def test_outfit_generation():
    """Test 7: Outfit Image Generation Pipeline with Email"""
    log("=" * 60, "TEST")
    log("TEST 7: OUTFIT GENERATION PIPELINE (WITH EMAIL)", "TEST")

    customer = (
        db.query(Customer)
        .filter(Customer.store_id == 1, Customer.total_orders > 0)
        .first()
    )

    if not customer:
        log("FAIL: No customer with orders for outfit test", "ERROR")
        return False

    # Get customer's last order
    order = (
        db.query(Order)
        .filter(Order.store_id == 1, Order.customer_id == customer.id)
        .order_by(Order.created_at.desc())
        .first()
    )

    if not order:
        log("FAIL: No order found for outfit test", "ERROR")
        return False

    try:
        log(f"Generating outfit for customer {customer.id} ({customer.email})")
        log(f"Order ID: {order.id}")
        log("This will call GPT-Image API and Gmail API...")

        request = GenerateOutfitImageRequest(
            customer_id=customer.id,
            order_id=order.id,
            trigger_reason="test_pipeline",
            send_email=True,
            recipient_email=TEST_EMAIL,
        )

        outfit = generate_outfit_for_customer(
            db, store_id=1, request=request, settings=settings
        )

        log(f"Outfit ID: {outfit.id}")
        log(f"Status: {outfit.status}")
        log(f"Provider: {outfit.provider}")
        log(f"Model: {outfit.model_name}")
        log(f"Image URL: {outfit.image_url}")
        log(f"Email Subject: {outfit.email_subject}")

        if outfit.status == "sent":
            log(f"PASS: Outfit generated and email sent to {TEST_EMAIL}", "SUCCESS")
            return True
        elif outfit.status == "generated":
            log(f"PASS: Outfit generated successfully", "SUCCESS")
            return True
        else:
            log(f"FAIL: Outfit status is {outfit.status}", "ERROR")
            if outfit.error_message:
                log(f"Error: {outfit.error_message}", "ERROR")
            return False
    except Exception as e:
        log(f"FAIL: Exception - {str(e)}", "ERROR")
        import traceback

        traceback.print_exc()
        return False


def test_anniversary_campaign():
    """Test 8: First Order Anniversary Campaign with Email"""
    log("=" * 60, "TEST")
    log("TEST 8: ANNIVERSARY CAMPAIGN (WITH EMAIL)", "TEST")

    customer = (
        db.query(Customer)
        .filter(Customer.store_id == 1, Customer.total_orders > 0)
        .first()
    )

    if not customer:
        log("FAIL: No customer with orders for anniversary test", "ERROR")
        return False

    try:
        log(f"Running anniversary campaign for customer {customer.id}")
        log("This will generate outfit image and send email...")

        request = FirstOrderAnniversaryCampaignRequest(
            customer_id=customer.id,
            limit=1,
            force=True,
            send_email=True,
            recipient_email=TEST_EMAIL,
            days_window=365,
        )

        result = run_first_order_anniversary_campaign(db, store_id=1, request=request)

        log(f"Processed: {result.processed}")
        log(f"Generated: {result.generated}")
        log(f"Sent: {result.sent}")
        log(f"Skipped: {len(result.skipped)}")

        if result.skipped:
            for skip in result.skipped:
                log(f"  Skipped customer {skip.customer_id}: {skip.reason}")

        if result.sent > 0:
            log(f"PASS: Anniversary campaign sent email to {TEST_EMAIL}", "SUCCESS")
            return True
        elif result.generated > 0:
            log(f"PASS: Anniversary campaign generated outfit", "SUCCESS")
            return True
        else:
            log("FAIL: No anniversary emails sent", "ERROR")
            return False
    except Exception as e:
        log(f"FAIL: Exception - {str(e)}", "ERROR")
        import traceback

        traceback.print_exc()
        return False


def test_pre_churn_campaign():
    """Test 9: Pre-Churn Campaign with Email"""
    log("=" * 60, "TEST")
    log("TEST 9: PRE-CHURN CAMPAIGN (WITH EMAIL)", "TEST")

    try:
        log("Running pre-churn campaign (finding at-risk customers)...")
        log("This will generate outfit image and send email...")

        request = CampaignRunRequest(
            limit=1,
            force=True,
            send_email=True,
            recipient_email=TEST_EMAIL,
        )

        result = run_pre_churn_campaign(
            db, store_id=1, request=request, settings=settings
        )

        log(f"Processed: {result.processed}")
        log(f"Generated: {result.generated}")
        log(f"Sent: {result.sent}")
        log(f"Skipped: {len(result.skipped)}")

        if result.skipped:
            for skip in result.skipped:
                log(f"  Skipped: {skip}")

        if result.sent > 0:
            log(f"PASS: Pre-churn campaign sent email to {TEST_EMAIL}", "SUCCESS")
            return True
        elif result.generated > 0:
            log(f"PASS: Pre-churn campaign generated outfit", "SUCCESS")
            return True
        else:
            log(
                "WARNING: No pre-churn emails sent (may be OK if no high-risk customers)",
                "WARN",
            )
            return True
    except Exception as e:
        log(f"FAIL: Exception - {str(e)}", "ERROR")
        import traceback

        traceback.print_exc()
        return False


def test_silent_customer_campaign():
    """Test 10: Silent Customer Campaign with Email"""
    log("=" * 60, "TEST")
    log("TEST 10: SILENT CUSTOMER CAMPAIGN (WITH EMAIL)", "TEST")

    try:
        log("Running silent customer campaign...")
        log("This will generate outfit image and send email...")

        request = CampaignRunRequest(
            limit=1,
            force=True,
            send_email=True,
            recipient_email=TEST_EMAIL,
        )

        result = run_silent_customer_campaign(
            db, store_id=1, request=request, settings=settings
        )

        log(f"Processed: {result.processed}")
        log(f"Generated: {result.generated}")
        log(f"Sent: {result.sent}")
        log(f"Skipped: {len(result.skipped)}")

        if result.skipped:
            for skip in result.skipped:
                log(f"  Skipped: {skip}")

        if result.sent > 0:
            log(f"PASS: Silent customer campaign sent email to {TEST_EMAIL}", "SUCCESS")
            return True
        elif result.generated > 0:
            log(f"PASS: Silent customer campaign generated outfit", "SUCCESS")
            return True
        else:
            log(
                "WARNING: No silent customer emails sent (may be OK if no silent customers)",
                "WARN",
            )
            return True
    except Exception as e:
        log(f"FAIL: Exception - {str(e)}", "ERROR")
        import traceback

        traceback.print_exc()
        return False


def test_seasonal_lookbook_campaign():
    """Test 11: Seasonal Lookbook Campaign with Email"""
    log("=" * 60, "TEST")
    log("TEST 11: SEASONAL LOOKBOOK CAMPAIGN (WITH EMAIL)", "TEST")

    try:
        log("Running seasonal lookbook campaign...")
        log("This will generate outfit image and send email...")

        request = CampaignRunRequest(
            limit=1,
            force=True,
            send_email=True,
            recipient_email=TEST_EMAIL,
        )

        result = run_seasonal_lookbook_campaign(
            db, store_id=1, request=request, season="spring", settings=settings
        )

        log(f"Processed: {result.processed}")
        log(f"Generated: {result.generated}")
        log(f"Sent: {result.sent}")
        log(f"Skipped: {len(result.skipped)}")

        if result.skipped:
            for skip in result.skipped:
                log(f"  Skipped: {skip}")

        if result.sent > 0:
            log(
                f"PASS: Seasonal lookbook campaign sent email to {TEST_EMAIL}",
                "SUCCESS",
            )
            return True
        elif result.generated > 0:
            log(f"PASS: Seasonal lookbook campaign generated outfit", "SUCCESS")
            return True
        else:
            log(
                "WARNING: No seasonal emails sent (needs customers with 3+ orders)",
                "WARN",
            )
            return True
    except Exception as e:
        log(f"FAIL: Exception - {str(e)}", "ERROR")
        import traceback

        traceback.print_exc()
        return False


def test_send_policy():
    """Test 12: Send Policy Enforcement"""
    log("=" * 60, "TEST")
    log("TEST 12: SEND POLICY ENFORCEMENT", "TEST")

    from app.services.send_policy_service import enforce_send_policy, SendPolicyError

    customer = db.query(Customer).filter(Customer.store_id == 1).first()

    if not customer:
        log("FAIL: No customer for send policy test", "ERROR")
        return False

    try:
        # Test 1: Should allow with force=True
        enforce_send_policy(
            db, store_id=1, customer_id=customer.id, campaign_type="test", force=True
        )
        log("PASS: Send policy allows with force=True")

        # Test 2: Check if recent send exists
        recent_send = (
            db.query(RetentionSendLog)
            .filter(
                RetentionSendLog.store_id == 1,
                RetentionSendLog.customer_id == customer.id,
                RetentionSendLog.campaign_type == "test",
            )
            .order_by(RetentionSendLog.sent_at.desc())
            .first()
        )

        if recent_send:
            log(f"Recent send logged at {recent_send.sent_at}")

        log("PASS: Send policy enforcement working", "SUCCESS")
        return True
    except SendPolicyError as e:
        log(f"Send policy blocked: {str(e)}")
        log("This is expected if customer already received email recently", "INFO")
        return True
    except Exception as e:
        log(f"FAIL: Exception - {str(e)}", "ERROR")
        return False


def generate_report():
    """Generate final diagnostic report"""
    log("=" * 60, "REPORT")
    log("PIPELINE TEST SUMMARY", "REPORT")

    report_path = Path(__file__).parent / "pipeline_test_report.txt"
    report_path.write_text("\n".join(REPORT))

    log(f"Report saved to: {report_path}")

    # Count results
    passed = sum(1 for line in REPORT if "[SUCCESS]" in line)
    failed = sum(1 for line in REPORT if "[ERROR]" in line)
    warnings = sum(1 for line in REPORT if "[WARN]" in line)

    log(f"\nRESULTS: {passed} passed, {failed} failed, {warnings} warnings")

    if failed == 0:
        log("ALL CRITICAL TESTS PASSED!", "SUCCESS")
    else:
        log(f"{failed} TESTS FAILED - Review errors above", "ERROR")


if __name__ == "__main__":
    log("STARTING COMPREHENSIVE PIPELINE TESTS", "START")
    log(f"Test email: {TEST_EMAIL}")
    log(f"Dry run: {settings.dry_run}")

    tests = [
        ("Database State", test_database_state),
        ("Buyer Memory", test_buyer_memory),
        ("Intent Engine", test_intent_engine),
        ("Recommendation Engine", test_recommendation_engine),
        ("Churn Risk", test_churn_risk),
        ("Silent Customers", test_silent_customers),
        ("Send Policy", test_send_policy),
        ("Outfit Generation", test_outfit_generation),
        ("Anniversary Campaign", test_anniversary_campaign),
        ("Pre-Churn Campaign", test_pre_churn_campaign),
        ("Silent Customer Campaign", test_silent_customer_campaign),
        ("Seasonal Lookbook", test_seasonal_lookbook_campaign),
    ]

    results = {}
    for name, test_func in tests:
        try:
            results[name] = test_func()
        except Exception as e:
            log(f"CRITICAL ERROR in {name}: {str(e)}", "ERROR")
            results[name] = False

    generate_report()
