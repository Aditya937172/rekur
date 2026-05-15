from __future__ import annotations

from typing import Any

from app.core.config import AppSettings, load_settings
from app.services.gmail_service import GmailServiceError, send_gmail_message
from app.services.sendgrid_service import SendGridServiceError, send_sendgrid_email


class EmailDeliveryError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def send_retention_email(
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
    provider = settings.retention_sender_provider.strip().lower()
    if provider == "sendgrid":
        try:
            return send_sendgrid_email(
                recipient_email=recipient_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
            inline_images=inline_images,
            settings=settings,
        )
        except SendGridServiceError as exc:
            raise EmailDeliveryError(str(exc), status_code=exc.status_code) from exc

    try:
        response = send_gmail_message(
            recipient_email=recipient_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
            inline_images=inline_images,
            settings=settings,
        )
        response["provider"] = "gmail"
        return response
    except GmailServiceError as exc:
        raise EmailDeliveryError(str(exc), status_code=exc.status_code) from exc
