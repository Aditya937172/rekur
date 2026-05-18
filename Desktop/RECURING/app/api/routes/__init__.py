from app.api.routes.buyer_memory import router as buyer_memory_router
from app.api.routes.email_events import router as email_events_router
from app.api.routes.events import router as events_router
from app.api.routes.gmail import router as gmail_router
from app.api.routes.intent import router as intent_router
from app.api.routes.messages import router as messages_router
from app.api.routes.orders import router as orders_router
from app.api.routes.outfits import router as outfits_router
from app.api.routes.recommendations import router as recommendations_router
from app.api.routes.replies import router as replies_router
from app.api.routes.retention import router as retention_router
from app.api.routes.stores import router as stores_router
from app.api.routes.webhooks import router as webhooks_router

__all__ = [
    "buyer_memory_router",
    "email_events_router",
    "events_router",
    "gmail_router",
    "intent_router",
    "messages_router",
    "orders_router",
    "outfits_router",
    "recommendations_router",
    "replies_router",
    "retention_router",
    "stores_router",
    "webhooks_router",
]
