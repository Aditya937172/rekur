# Seasonal Lookbook Campaign System

**Last Updated:** 2026-05-01  
**Status:** ✅ IMPLEMENTED

---

## Overview

The Seasonal Lookbook Campaign system automatically generates personalized styling emails for customers on a quarterly basis, showing them outfit combinations from their existing wardrobe styled for the upcoming season.

---

## How It Works

### 1. Quarterly Cron Execution

**Trigger:** First week of each season (7-day window)  
**Time:** 10:00 AM customer local time  
**Frequency:** 4x per year per hemisphere

**Schedule by Hemisphere:**

| Season | Northern Hemisphere | Southern Hemisphere |
|--------|---------------------|---------------------|
| Spring | March 1-7 | September 1-7 |
| Summer | June 1-7 | December 1-7 |
| Fall | September 1-7 | March 1-7 |
| Winter | December 1-7 | June 1-7 |

### 2. Eligibility Criteria

Customers must meet ALL of the following:

- ✅ Active customer status
- ✅ Minimum 3 completed orders
- ✅ Has wardrobe items in buyer memory
- ✅ Has NOT received this season's lookbook yet
- ✅ Country field populated (for hemisphere detection)

### 3. FashionCLIP Outfit Detection

**Process:**

1. Load all owned products from `BuyerMemory.wardrobe_items_json`
2. Categorize products:
   - **Top:** shirts, tees, sweaters, hoodies
   - **Bottom:** jeans, trousers, shorts, skirts
   - **Outer:** jackets, coats, blazers
   - **Accessories:** scarves, hats, belts
3. Use FashionCLIP to score combinations:
   - Category diversity (need top + bottom minimum)
   - Style coherence via semantic embeddings
   - Gender match (if customer gender inferred)
4. Select top 4 products for the combination

### 4. Image Generation

**Provider:** Seedream V4 (RunPod) or GPT-Image-2 (Evolink)

**Prompt Structure:**
```
One seasonal fashion lookbook image, exactly 3 styling options in one cohesive triptych.
Use only these already-owned wardrobe pieces: [product titles].
Season: [spring/summer/fall/winter]. Style keywords: [seasonal keywords].
Show their best wardrobe combination styled three ways:
  - morning casual
  - afternoon outing
  - evening elevated
Realistic premium D2C campaign, natural attractive model.
No text, no logos, no labels, no watermarks.
Customer style context: [memory summary].
```

### 5. Email Generation (LLM)

**Model:** Groq LLaMA 3.3 70B  
**Template:**

```
Write a seasonal lookbook email for a clothing brand customer.
They already own all the pieces shown: [product titles].
This is not about buying anything new.
Frame it as discovering outfits they already have.
Incoming season: [season].
Their style aesthetic: [favorite categories].
Acknowledge their specific style aesthetic by name.
Sound like a friend who found great combinations in their wardrobe.
GenZ tone. Max 5 sentences introduction.
No selling. No product links. Pure styling value.
One subtle final line: missing something for [season]? we might have it.
```

### 6. Gap Product Recommendation

**Logic:**

1. Get product recommendations for customer
2. Filter out already-owned products
3. Score by seasonal keywords match
4. Return top 1 product as "gap" suggestion

**Final Email Line:**
```
missing something for spring? we might have it.
```
(Links to personalized product recommendation page)

---

## Geographic Region Detection

### Hemisphere Mapping

```python
# Northern Hemisphere
["US", "CA", "UK", "DE", "FR", "IT", "ES", "IN", "JP", "CN", "RU"]

# Southern Hemisphere
["AU", "NZ", "AR", "BR", "ZA", "CL"]

# Equatorial (minimal seasonal variation)
["SG", "MY", "TH", "ID", "KE", "NG"]
```

### Local Time Scheduling

Default send window: **10:00 AM ± 2 hours** (8 AM - 12 PM local)

**Implementation:**
- Customer's country → hemisphere → season
- Timezone offset → local hour calculation
- Send when local hour is within optimal window

---

## Tracking & Analytics

### Metrics Tracked

| Metric | Event Type | Score |
|--------|-----------|-------|
| Email sent | `email_sent` | — |
| Email opened | `email_opened` | 10 pts |
| Link clicked | `email_clicked` | 15 pts |
| Image saved | `image_saved` | 20 pts |
| Image shared | `image_shared` | 25 pts |
| Gap link clicked | `gap_link_clicked` | 15 pts |
| Gap product viewed | `gap_product_viewed` | 10 pts |
| Gap added to cart | `gap_product_added_to_cart` | 20 pts |
| Gap purchased | `gap_product_purchased` | 50 pts |

### Engagement Score Formula

```
Total Score = Sum of all event scores (max 200)
Score = min(sum, 200)
```

### Database Tables

**`seasonal_campaign_analytics`**
- Per-customer campaign metrics
- Conversion funnel tracking
- Aggregated engagement score

**`campaign_engagement_events`**
- Individual event records
- Detailed tracking (timestamps, URLs, user agents)

---

## API Endpoints

### Manual Campaign Trigger

```http
POST /stores/{store_id}/retention/seasonal-lookbook/run
Content-Type: application/json

{
  "customer_id": null,  // null = all eligible customers
  "limit": 25,
  "send_email": true,
  "force": false,
  "season": "spring"  // optional override
}
```

### Analytics Dashboard

```http
GET /stores/{store_id}/analytics/seasonal?season=spring&year=2026

Response:
{
  "total_sent": 500,
  "open_rate": 0.45,
  "click_rate": 0.12,
  "save_rate": 0.08,
  "share_rate": 0.03,
  "gap_click_rate": 0.15,
  "gap_conversion_rate": 0.04,
  "avg_engagement_score": 42.5
}
```

---

## Improvements & Extensions

### 1. **Weather Integration** 🌡️

**Current:** Fixed calendar seasons  
**Improved:** Real-time weather-based campaign timing

```python
# Integrate weather API
def get_local_season(customer_location):
    temperature = get_current_temperature(customer_location)
    
    if temperature > 25:  # Celsius
        return "summer_styles"
    elif temperature > 15:
        return "transitional_layers"
    else:
        return "cold_weather_gear"
```

**Benefit:** Send lookbooks when weather actually matches the styling.

---

### 2. **Occasion-Based Styling** 🎉

**Current:** 3 generic styling scenarios  
**Improved:** Personalized occasion detection

**Implementation:**
- Analyze purchase tags for occasion keywords
- Detect: "office", "weekend", "date_night", "vacation", "athletic"
- Generate occasion-specific styling prompts

```python
occasions = detect_occasions(customer_wardrobe)
# Returns: ["casual", "office", "date_night"]

prompt = f"Style for {customer_name}'s lifestyle: {occasions}"
```

---

### 3. **Color Harmony Analysis** 🎨

**Current:** Basic color extraction  
**Improved:** Full color palette coordination

**Implementation:**
- Use FashionCLIP to extract dominant colors from product images
- Build customer's color wheel from wardrobe
- Identify color harmonies (complementary, analogous, triadic)

```python
def generate_outfit_by_color_harmony(wardrobe):
    color_wheel = extract_customer_colors(wardrobe)
    harmonies = find_color_harmonies(color_wheel)
    
    for palette in harmonies:
        match_products_by_color(palette)
```

**Email Enhancement:**
```
"I noticed your wardrobe leans blues and neutrals - 
 I found a combination that brings out those tones beautifully."
```

---

### 4. **A/B Testing Framework** 🧪

**Current:** Single email variant  
**Improved:** Multi-variant testing

**Test Dimensions:**

| Dimension | Variant A | Variant B |
|-----------|-----------|-----------|
| Subject line | "found a spring outfit..." | "your spring wardrobe, unlocked" |
| Email length | 5 sentences | 3 sentences |
| Image style | Triptych | Single hero image |
| Gap suggestion | Yes | No |
| Tone | Casual | Polished |

**Implementation:**
```python
experiment = ab_test_manager.assign_variant(customer_id)
email_body = generate_email(variant=experiment.variant)
```

---

### 5. **Multi-Product Cross-Store Recommendations** 🛍️

**Current:** Single-store products only  
**Improved:** Partnership network

**Use Case:** Customer bought jacket from Store A, recommend pants from Store B (partner brand)

**Implementation:**
- Create product embedding space across brands
- Match compatibility via FashionCLIP
- Revenue-sharing with partner brands

---

### 6. **Interactive Styling Widget** 📱

**Current:** Static image in email  
**Improved:** Interactive styling tool

**Features:**
- Click to swap individual pieces
- "Try this look" button → adds all items to cart
- Save to "My Looks" collection
- Share on social media
- Request similar alternatives

**Technical:**
- Embedded React widget in email (AMP for Email)
- Links to web app for full functionality

---

### 7. **Purchase Cycle Prediction** 📊

**Current:** Fixed quarterly timing  
**Improved:** Predictive timing based on purchase patterns

**Logic:**
```
Customer last purchased: 60 days ago
Average purchase interval: 90 days
→ Send lookbook at day 75 (before next purchase window)
```

**Features:**
- Early replenishment suggestions
- "Time to refresh your X" emails
- Coordinated with sale events

---

### 8. **Social Media Integration** 📸

**Current:** No social features  
**Improved:** Social sharing with attribution

**Implementation:**
- One-click share to Instagram Stories
- UGC (User Generated Content) collection
- "Customer Style Gallery" on storefront
- Influencer collaboration opportunities

**Tracking:**
```python
track_image_shared(
    outfit_image_id=outfit.id,
    platform="instagram_stories",
    customer_id=customer.id
)
```

---

### 9. **Sustainability Scoring** 🌱

**Current:** No sustainability focus  
**Improved:** Environmental impact awareness

**Features:**
- Calculate "outfits from wardrobe" vs "new purchases"
- Carbon footprint saved by styling existing pieces
- Badge system for sustainable fashion behaviors

**Email Addition:**
```
" styling pieces you already own has saved 2.3kg of carbon 
 compared to buying new. You're building a more sustainable wardrobe!"
```

---

### 10. **Machine Learning Ranking** 🤖

**Current:** Rule-based outfit selection  
**Improved:** ML model trained on engagement data

**Training Data:**
- Which outfits got highest save rate?
- Which combinations led to gap purchases?
- Which email copy had highest CTR?

**Model:**
```python
model = train_engagement_predictor(
    features=[
        "category_diversity",
        "color_harmony_score",
        "seasonal_relevance",
        "product_age",
    ],
    target="engagement_score"
)

best_outfit = model.predict_top_combination(wardrobe_products)
```

---

## Configuration

### Environment Variables

```bash
# Scheduler
SEASONAL_CAMPAIGN_ENABLED=true
SEASONAL_CAMPAIGN_LIMIT_PER_RUN=100

# Image Generation
IMAGE_PROVIDER=runpod_seedream
RUNPOD_SEEDREAM_ENDPOINT_ID=xxx

# Tracking
TRACKING_ENABLED=true
GAP_LINK_TRACKING=true
```

### Scaling Considerations

**At 10,000 customers:**
- 4 campaigns × 10,000 = 40,000 emails/year
- Image generation: 40,000 × 90 seconds = 1,000 hours
- **Solution:** Parallel processing with Celery, 3-day campaign window

**At 100,000 customers:**
- Batch customers by timezone
- Pre-generate images 1 week before campaign
- Cache hit rate should reach 40%+ after first year

---

## Testing

### Unit Tests

```bash
pytest tests/test_seasonal_utils.py
pytest tests/test_seasonal_scheduler.py
pytest tests/test_seasonal_analytics.py
```

### Integration Tests

```bash
# Test full pipeline for single customer
python -c "
from app.services.seasonal_lookbook_service import generate_seasonal_lookbook_for_customer
from app.db.session import get_db

db = next(get_db())
result = await generate_seasonal_lookbook_for_customer(
    db,
    store_id=1,
    customer_id=98,
    season='spring',
    send_email=True,
    recipient_email='test@example.com'
)
print(result)
"
```

---

## Files Created

```
app/
├── utils/
│   └── season_utils.py              ✅ Hemisphere & season detection
├── services/
│   ├── seasonal_scheduler.py        ✅ Customer segmentation & scheduling
│   ├── seasonal_lookbook_service.py ✅ Main campaign logic
│   └── seasonal_analytics.py        ✅ Tracking & metrics
└── scheduler/
    └── cron_scheduler.py            ✅ APScheduler configuration
```

---

## Next Steps

1. ✅ **Implement tracking** - Done (seasonal_analytics.py)
2. ⏳ **Add Celery for async processing** - Recommended for scale
3. ⏳ **Create admin dashboard** - Monitor campaign performance
4. ⏳ **Implement A/B testing** - Optimize email performance
5. ⏳ **Weather API integration** - Improve relevance

---

## Performance Targets

| Metric | Target | Current |
|--------|--------|---------|
| Open Rate | >40% | TBD |
| Click Rate | >15% | TBD |
| Save Rate | >5% | TBD |
| Share Rate | >1% | TBD |
| Gap Conversion | >3% | TBD |
| Cache Hit Rate | >40% | 0% (new system) |

---

## Conclusion

The Seasonal Lookbook Campaign system is now fully implemented with:

✅ Geographic region detection (hemisphere-aware)  
✅ FashionCLIP outfit combination detection  
✅ Quarterly automated scheduling  
✅ LLM-generated personalized emails  
✅ Gap product recommendations  
✅ Comprehensive tracking & analytics  

The system is ready for production deployment with APScheduler integration into the FastAPI app.
