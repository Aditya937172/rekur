# RECURING - Shopify Retention Platform

AI-powered customer retention platform for Shopify stores with personalized styling recommendations.

## Features

### Core Intelligence
- **Intent Engine** - Scores customer buying signals (product views, cart additions, session frequency)
- **Churn Detection** - Identifies at-risk customers with 100-point scoring system
- **Buyer Memory** - Aggregates purchase history, style preferences, and browsing behavior
- **Recommendation Engine** - Semantic product matching via FashionCLIP

### Campaign Types
- **Pre-Churn Campaign** - Automated re-engagement for customers with high churn scores
- **Anniversary Campaign** - First-order anniversary celebration with similar product recommendations
- **Seasonal Lookbook** - Quarterly styling emails showing owned products styled 3 ways
- **Silent Customer Campaign** - Re-engagement for high-open/low-click customers

### AI Features
- **Outfit Generation** - GPT-Image-2 / Seedream V4 creates personalized styling images
- **Email Copy** - Groq LLaMA 3.3 70B generates personalized messaging
- **Gender Matching** - Infers customer gender from purchase history
- **Geographic Seasons** - Hemisphere-aware seasonal campaigns (Northern/Southern)

## Tech Stack

- **Backend:** FastAPI + SQLAlchemy
- **Database:** SQLite (production: PostgreSQL)
- **AI/LLM:** Groq (LLaMA 3.3 70B)
- **Image Generation:** Evolink GPT-Image-2 / RunPod Seedream V4
- **Embeddings:** FashionCLIP for product similarity
- **Email:** Gmail API / SendGrid
- **Scheduler:** APScheduler for automated campaigns
- **Auth:** Nango for Shopify OAuth

## Installation

```bash
# Clone repository
git clone https://github.com/Aditya937172/rekur.git
cd rekur

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env
# Edit .env with your credentials

# Run migrations
alembic upgrade head

# Start server
uvicorn app.main:app --reload
```

## Configuration

Required environment variables (see `.env.example`):

```bash
# Shopify
SHOPIFY_STORE_DOMAIN=your-store.myshopify.com
SHOPIFY_ADMIN_ACCESS_TOKEN=shpat_xxx

# AI Services
GROQ_API_KEY=gsk_xxx
IMAGE_API_KEY=xxx

# Email
GMAIL_SENDER_EMAIL=your-email@gmail.com
GMAIL_REFRESH_TOKEN=xxx

# Optional
SENDGRID_API_KEY=xxx
RUNPOD_API_KEY=xxx
```

## API Endpoints

### Campaigns
```http
POST /stores/{id}/retention/pre-churn/run
POST /stores/{id}/retention/silent-customers/run
POST /stores/{id}/retention/seasonal-lookbook/run
POST /stores/{id}/outfits/anniversary/run
```

### Intelligence
```http
GET /stores/{id}/intent?limit=100
GET /stores/{id}/recommendations?customer_id=123
GET /stores/{id}/retention/churn-risk
GET /stores/{id}/buyer-memory/{customer_id}
```

### Analytics
```http
GET /stores/{id}/analytics/seasonal?season=spring
```

## Project Structure

```
app/
├── api/routes/          # FastAPI endpoints
├── core/                # Configuration
├── db/                  # Database session
├── models/              # SQLAlchemy models
├── schemas/             # Pydantic schemas
├── services/            # Business logic
│   ├── buyer_memory_service.py
│   ├── churn_detection.py
│   ├── intent_engine.py
│   ├── recommendation_engine.py
│   ├── outfit_service.py
│   ├── seasonal_lookbook_service.py
│   └── gender_service.py
├── scheduler/           # Cron jobs
└── utils/               # Helpers
```

## Key Algorithms

### Churn Scoring (0-100)
- **Purchase Frequency Drop** (35 pts) - Customer stopped ordering at usual interval
- **Email Engagement Drop** (30 pts) - Open rate decreased 50%+
- **Site Visit Drop** (20 pts) - Visits less frequent than baseline
- **Engagement Depth Drop** (15 pts) - Less time/interaction per visit

### Outfit Generation
1. Load customer's wardrobe from buyer memory
2. Use FashionCLIP to find best outfit combination
3. Check vector cache for existing image
4. Generate new image showing 3 styling variations
5. Send personalized email with gap product recommendation

### Seasonal Campaign Logic
- Detects customer hemisphere from country
- Sends appropriate seasonal content
- Northern: Spring (Mar), Summer (Jun), Fall (Sep), Winter (Dec)
- Southern: Spring (Sep), Summer (Dec), Fall (Mar), Winter (Jun)

## Testing

```bash
# Run pipeline tests
python test_pipelines.py

# Check specific service
pytest tests/test_intent_engine.py
```

## Production Deployment

### Requirements
- Python 3.11+
- PostgreSQL (recommended for scale)
- Redis (for Celery task queue)
- Docker (optional)

### Scaling Tips
- Use Celery for async image generation
- Add database connection pooling
- Implement rate limiting for external APIs
- Enable caching for FashionCLIP embeddings

## Documentation

- [Diagnostic Report](DIAGNOSTIC_REPORT.md) - Full system analysis
- [Seasonal System](SEASONAL_LOOKBOOK_SYSTEM.md) - Quarterly campaign details
- [Gender Fix](GENDER_ANNIVERSARY_FIX.md) - Gender matching implementation
- [Nango Setup](README_NANGO_SHOPIFY_SETUP.md) - Shopify OAuth integration

## Cost Estimation

Per 1,000 customers per month:
- Image Generation: $50-100
- LLM (Groq): $5-10
- Email (Gmail free): $0
- **Total: ~$60-110/month**

## Security

- All credentials excluded via `.gitignore`
- Environment variables for secrets
- OAuth2 for Gmail/Shopify
- No sensitive data in commits

## License

MIT

## Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing`)
5. Open Pull Request

## Author

Aditya Palghar

## Acknowledgments

- Groq for fast LLM inference
- FashionCLIP for product embeddings
- Nango for OAuth integration
- Shopify Admin API
