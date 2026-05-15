from __future__ import annotations

import base64
from typing import Any

import requests

from app.core.config import AppSettings, load_settings


class SendGridServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def send_sendgrid_email(
    *,
    recipient_email: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    inline_images: list[dict[str, Any]] | None = None,
    settings: AppSettings | None = None,
) -> dict[str, Any]:
    settings = settings or load_settings()
    ensure_sendgrid_configured(settings)
    payload = {
        "personalizations": [
            {
                "to": [{"email": recipient_email}],
                "subject": subject,
            }
        ],
        "from": {
            "email": settings.sendgrid_from_email,
            "name": settings.sendgrid_from_name,
        },
        "content": email_content(body_text, body_html),
        "tracking_settings": {
            "click_tracking": {"enable": True, "enable_text": True},
            "open_tracking": {"enable": True},
            "subscription_tracking": {"enable": False},
        },
    }
    sendgrid_attachments = build_sendgrid_attachments(
        attachments=attachments or [],
        inline_images=inline_images or [],
    )
    if sendgrid_attachments:
        payload["attachments"] = sendgrid_attachments

    response = requests.post(
        f"{settings.sendgrid_base_url}/mail/send",
        headers={
            "Authorization": f"Bearer {settings.sendgrid_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=settings.sendgrid_timeout_seconds,
    )
    if response.status_code >= 400:
        raise SendGridServiceError(
            f"SendGrid send failed with HTTP {response.status_code}: {response.text[:500]}",
            status_code=502,
        )
    return {
        "id": response.headers.get("X-Message-Id"),
        "status_code": response.status_code,
        "provider": "sendgrid",
    }


def email_content(body_text: str, body_html: str | None) -> list[dict[str, str]]:
    content = [{"type": "text/plain", "value": body_text}]
    if body_html:
        content.append({"type": "text/html", "value": body_html})
    return content


def build_sendgrid_attachments(
    *,
    attachments: list[dict[str, Any]],
    inline_images: list[dict[str, Any]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for attachment in attachments:
        row = attachment_to_sendgrid(attachment, disposition="attachment")
        if row:
            rows.append(row)
    for image in inline_images:
        row = attachment_to_sendgrid(image, disposition="inline")
        if row:
            row["content_id"] = str(image.get("cid") or "inline-image")
            rows.append(row)
    return rows


def attachment_to_sendgrid(
    attachment: dict[str, Any],
    *,
    disposition: str,
) -> dict[str, str] | None:
    content = attachment.get("content")
    if not content:
        return None
    if isinstance(content, str):
        raw = content.encode("utf-8")
    else:
        raw = bytes(content)
    return {
        "content": base64.b64encode(raw).decode("ascii"),
        "type": str(attachment.get("mime_type") or "application/octet-stream"),
        "filename": str(attachment.get("filename") or "attachment"),
        "disposition": disposition,
    }


def ensure_sendgrid_configured(settings: AppSettings) -> None:
    if not settings.sendgrid_api_key or not settings.sendgrid_from_email:
        raise SendGridServiceError(
            "SENDGRID_API_KEY and SENDGRID_FROM_EMAIL are required for SendGrid delivery.",
            status_code=400,
        )
