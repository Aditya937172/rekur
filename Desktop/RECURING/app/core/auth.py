from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import AppSettings, load_settings
from app.db.session import get_db
from app.models import AppUser, GeneratedMessage, GeneratedOutfitImage, Store, StoreOwnership


TOKEN_VERSION = "v1"
DEFAULT_DEV_SECRET = "dev-insecure-retention-auth-secret-change-me"


def normalize_email(email: str) -> str:
    return email.strip().lower()


def create_access_token(
    user: AppUser,
    *,
    purpose: str = "access",
    ttl_minutes: int | None = None,
    settings: AppSettings | None = None,
) -> str:
    settings = settings or load_settings()
    _validate_auth_secret(settings)
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=ttl_minutes or settings.app_auth_token_ttl_minutes
    )
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "purpose": purpose,
        "exp": int(expires_at.timestamp()),
        "iat": int(time.time()),
    }
    return _sign_payload(payload, settings.app_auth_secret_key)


def decode_token(
    token: str,
    *,
    purpose: str = "access",
    settings: AppSettings | None = None,
) -> dict[str, Any]:
    settings = settings or load_settings()
    _validate_auth_secret(settings)
    try:
        version, payload_b64, signature_b64 = token.split(".", 2)
    except ValueError as exc:
        raise _unauthorized("Invalid auth token.") from exc
    if version != TOKEN_VERSION:
        raise _unauthorized("Unsupported auth token version.")

    expected_signature = _signature(payload_b64, settings.app_auth_secret_key)
    if not hmac.compare_digest(signature_b64, expected_signature):
        raise _unauthorized("Invalid auth token signature.")

    try:
        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _unauthorized("Invalid auth token payload.") from exc

    if payload.get("purpose") != purpose:
        raise _unauthorized("Invalid auth token purpose.")
    if int(payload.get("exp") or 0) < int(time.time()):
        raise _unauthorized("Auth token expired.")
    return payload


def get_or_create_app_user(
    db: Session,
    *,
    email: str,
    name: str | None = None,
    auth_provider: str = "local",
    external_id: str | None = None,
) -> AppUser:
    normalized_email = normalize_email(email)
    if "@" not in normalized_email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A valid email is required.",
        )
    user = db.scalar(select(AppUser).where(AppUser.email == normalized_email))
    if user:
        if name and user.name != name:
            user.name = name
        if external_id and user.external_id != external_id:
            user.external_id = external_id
        return user

    user = AppUser(
        email=normalized_email,
        name=name,
        auth_provider=auth_provider,
        external_id=external_id,
    )
    db.add(user)
    db.flush()
    return user


def add_store_owner(
    db: Session,
    *,
    user_id: int,
    store_id: int,
    role: str = "owner",
) -> StoreOwnership:
    ownership = db.scalar(
        select(StoreOwnership).where(
            StoreOwnership.user_id == user_id,
            StoreOwnership.store_id == store_id,
        )
    )
    if ownership:
        return ownership
    ownership = StoreOwnership(user_id=user_id, store_id=store_id, role=role)
    db.add(ownership)
    db.flush()
    return ownership


def ensure_user_owns_store(db: Session, user_id: int, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Store was not found.",
        )
    ownership = db.scalar(
        select(StoreOwnership.id).where(
            StoreOwnership.user_id == user_id,
            StoreOwnership.store_id == store_id,
        )
    )
    if not ownership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Store was not found.",
        )
    return store


def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> AppUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _unauthorized("Bearer token is required.")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token, purpose="access")
    user_id = int(payload.get("sub") or 0)
    user = db.get(AppUser, user_id)
    if not user:
        raise _unauthorized("Authenticated user was not found.")
    return user


def require_internal_admin(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> bool:
    settings = load_settings()
    supplied = x_internal_token
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization.split(" ", 1)[1].strip()
    if settings.internal_admin_token:
        if hmac.compare_digest(supplied or "", settings.internal_admin_token):
            return True
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid internal admin token is required.",
        )
    if settings.environment.lower() in {"prod", "production"}:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="INTERNAL_ADMIN_TOKEN must be configured in production.",
        )
    return True


def require_store_access(
    store_id: int,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Store:
    return ensure_user_owns_store(db, current_user.id, store_id)


def require_message_access(
    message_id: int,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GeneratedMessage:
    message = db.get(GeneratedMessage, message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message was not found.",
        )
    ensure_user_owns_store(db, current_user.id, message.store_id)
    return message


def require_outfit_access(
    outfit_id: int,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GeneratedOutfitImage:
    outfit = db.get(GeneratedOutfitImage, outfit_id)
    if not outfit:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Outfit was not found.",
        )
    ensure_user_owns_store(db, current_user.id, outfit.store_id)
    return outfit


def _sign_payload(payload: dict[str, Any], secret: str) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    payload_b64 = _b64encode(payload_json.encode("utf-8"))
    return f"{TOKEN_VERSION}.{payload_b64}.{_signature(payload_b64, secret)}"


def _signature(payload_b64: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _b64encode(digest)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _validate_auth_secret(settings: AppSettings) -> None:
    if settings.environment.lower() in {"prod", "production"}:
        if not settings.app_auth_secret_key or settings.app_auth_secret_key == DEFAULT_DEV_SECRET:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="APP_AUTH_SECRET_KEY must be configured in production.",
            )


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )
