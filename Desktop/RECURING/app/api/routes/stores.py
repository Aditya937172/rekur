from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Customer, Order, Product, Store, SyncRun
from app.schemas import (
    StoreCreate,
    StoreDashboard,
    StoreRead,
    SyncResult,
    SyncRunRead,
    TrackingInstallResult,
)
from app.services.shopify_setup_service import ShopifySetupError, install_tracking_script
from app.services.sync_service import SyncServiceError, sync_store


router = APIRouter(prefix="/stores", tags=["stores"])


@router.post("", response_model=StoreRead, status_code=status.HTTP_201_CREATED)
def create_store(payload: StoreCreate, db: Session = Depends(get_db)) -> Store:
    store = Store(
        name=payload.name,
        nango_connection_id=payload.nango_connection_id,
        shopify_store_domain=payload.shopify_store_domain,
    )
    db.add(store)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A store with this domain or Nango connection already exists.",
        ) from exc
    db.refresh(store)
    return store


@router.post("/{store_id}/sync", response_model=SyncResult)
def sync_store_endpoint(store_id: int, db: Session = Depends(get_db)) -> SyncResult:
    try:
        summary = sync_store(db, store_id)
    except SyncServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return SyncResult(
        status=summary.status,
        products_synced=summary.products_synced,
        customers_synced=summary.customers_synced,
        orders_synced=summary.orders_synced,
    )


@router.post("/{store_id}/install-tracking", response_model=TrackingInstallResult)
def install_tracking_endpoint(
    store_id: int,
    db: Session = Depends(get_db),
) -> TrackingInstallResult:
    try:
        summary = install_tracking_script(db, store_id)
    except ShopifySetupError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return TrackingInstallResult(
        status=summary.status,
        store_id=summary.store_id,
        tracking_installed=summary.tracking_installed,
        tracking_installed_at=summary.tracking_installed_at,
        script_url=summary.script_url,
        message=summary.message,
    )


@router.get("/{store_id}/dashboard", response_model=StoreDashboard)
def get_store_dashboard(
    store_id: int,
    db: Session = Depends(get_db),
) -> StoreDashboard:
    ensure_store_exists(db, store_id)
    products = db.scalar(
        select(func.count(Product.id)).where(Product.store_id == store_id)
    ) or 0
    customers = db.scalar(
        select(func.count(Customer.id)).where(Customer.store_id == store_id)
    ) or 0
    orders = db.scalar(select(func.count(Order.id)).where(Order.store_id == store_id)) or 0
    last_sync = db.scalar(
        select(SyncRun.finished_at)
        .where(SyncRun.store_id == store_id, SyncRun.status == "success")
        .order_by(SyncRun.finished_at.desc())
        .limit(1)
    )
    return StoreDashboard(
        products=products,
        customers=customers,
        orders=orders,
        last_sync=last_sync,
    )


@router.get("/{store_id}/sync/status", response_model=SyncRunRead)
def get_sync_status(store_id: int, db: Session = Depends(get_db)) -> SyncRun:
    ensure_store_exists(db, store_id)
    sync_run = db.scalar(
        select(SyncRun)
        .where(SyncRun.store_id == store_id)
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    )
    if not sync_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No sync run exists for this store.",
        )
    return sync_run


def ensure_store_exists(db: Session, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Store {store_id} was not found.",
        )
    return store
