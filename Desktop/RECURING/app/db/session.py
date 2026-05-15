from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import load_settings
from app.db.base import Base


settings = load_settings()

connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    run_sqlite_compat_migrations()


def run_sqlite_compat_migrations() -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    inspector = inspect(engine)
    if "stores" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("stores")}
    with engine.begin() as connection:
        if "tracking_installed" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE stores "
                    "ADD COLUMN tracking_installed BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        if "tracking_installed_at" not in columns:
            connection.execute(
                text("ALTER TABLE stores ADD COLUMN tracking_installed_at DATETIME")
            )

        if "products" in inspector.get_table_names():
            product_columns = {
                column["name"] for column in inspector.get_columns("products")
            }
            if "handle" not in product_columns:
                connection.execute(
                    text("ALTER TABLE products ADD COLUMN handle VARCHAR(500)")
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_products_handle "
                    "ON products (handle)"
                )
            )

        if "orders" in inspector.get_table_names():
            order_columns = {
                column["name"] for column in inspector.get_columns("orders")
            }
            if "fulfillment_status" not in order_columns:
                connection.execute(
                    text("ALTER TABLE orders ADD COLUMN fulfillment_status VARCHAR(64)")
                )
            if "delivered_at" not in order_columns:
                connection.execute(
                    text("ALTER TABLE orders ADD COLUMN delivered_at DATETIME")
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_orders_fulfillment_status "
                    "ON orders (fulfillment_status)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_orders_delivered_at "
                    "ON orders (delivered_at)"
                )
            )

        if "generated_messages" in inspector.get_table_names():
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_generated_messages_store_id "
                    "ON generated_messages (store_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_generated_messages_customer_id "
                    "ON generated_messages (customer_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_generated_messages_status "
                    "ON generated_messages (status)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_generated_messages_store_status "
                    "ON generated_messages (store_id, status)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_generated_messages_store_customer_product_status "
                    "ON generated_messages "
                    "(store_id, customer_id, product_id, status)"
                )
            )

        if "generated_outfit_images" in inspector.get_table_names():
            outfit_columns = {
                column["name"]
                for column in inspector.get_columns("generated_outfit_images")
            }
            if "task_id" not in outfit_columns:
                connection.execute(
                    text("ALTER TABLE generated_outfit_images ADD COLUMN task_id VARCHAR(255)")
                )
            if "task_status" not in outfit_columns:
                connection.execute(
                    text(
                        "ALTER TABLE generated_outfit_images "
                        "ADD COLUMN task_status VARCHAR(64)"
                    )
                )
            if "task_progress" not in outfit_columns:
                connection.execute(
                    text(
                        "ALTER TABLE generated_outfit_images "
                        "ADD COLUMN task_progress INTEGER"
                    )
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_generated_outfit_images_task_id "
                    "ON generated_outfit_images (task_id)"
                )
            )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
