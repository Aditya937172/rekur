from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

import requests

from app.core.config import AppSettings, load_settings


class GmailServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def build_gmail_auth_url(
    *,
    settings: AppSettings | None = None,
    state: str | None = None,
) -> str:
    settings = settings or load_settings()
    ensure_oauth_client_configured(settings)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.gmail_redirect_uri,
        "response_type": "code",
        "scope": settings.gmail_send_scope,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if state:
        params["state"] = state
    return f"{settings.gmail_auth_uri}?{urlencode(params)}"


def exchange_code_for_tokens(
    code: str,
    *,
    settings: AppSettings | None = None,
) -> dict[str, Any]:
    settings = settings or load_settings()
    ensure_oauth_client_configured(settings)
    response = requests.post(
        settings.gmail_token_uri,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.gmail_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=settings.gmail_timeout_seconds,
    )
    if response.status_code >= 400:
        raise GmailServiceError(
            google_error_message("Gmail token exchange failed", response),
            status_code=502,
        )
    return response.json()


def refresh_access_token(*, settings: AppSettings | None = None) -> str:
    settings = settings or load_settings()
    ensure_oauth_client_configured(settings)
    if not settings.gmail_refresh_token:
        raise GmailServiceError(
            "GMAIL_REFRESH_TOKEN is missing. Open /gmail/auth-url, complete Google consent, then exchange the code and add the refresh token to .env.",
            status_code=400,
        )

    response = requests.post(
        settings.gmail_token_uri,
        data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": settings.gmail_refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=settings.gmail_timeout_seconds,
    )
    if response.status_code >= 400:
        raise GmailServiceError(
            google_error_message("Gmail access token refresh failed", response),
            status_code=502,
        )

    access_token = response.json().get("access_token")
    if not access_token:
        raise GmailServiceError(
            "Google token response did not include an access token.",
            status_code=502,
        )
    return str(access_token)


def send_gmail_message(
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
    ensure_sender_configured(settings)
    access_token = refresh_access_token(settings=settings)
    raw_message = build_raw_message(
        sender_email=settings.gmail_sender_email or "",
        recipient_email=recipient_email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
        inline_images=inline_images,
    )

    response = requests.post(
        f"{settings.gmail_api_base_url}/users/me/messages/send",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"raw": raw_message},
        timeout=settings.gmail_timeout_seconds,
    )
    if response.status_code >= 400:
        raise GmailServiceError(
            google_error_message("Gmail send failed", response),
            status_code=502,
        )
    return response.json()


def build_raw_message(
    *,
    sender_email: str,
    recipient_email: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    inline_images: list[dict[str, Any]] | None = None,
) -> str:
    message = EmailMessage()
    message["To"] = recipient_email
    message["From"] = sender_email
    message["Subject"] = subject
    message.set_content(body_text)
    if body_html:
        message.add_alternative(body_html, subtype="html")
        html_part = message.get_payload()[-1]
        for inline_image in inline_images or []:
            content = inline_image.get("content")
            if not content:
                continue
            maintype, subtype = parse_mime_type(
                str(inline_image.get("mime_type") or "image/png")
            )
            cid = str(inline_image.get("cid") or "inline-image")
            html_part.add_related(
                content,
                maintype=maintype,
                subtype=subtype,
                cid=f"<{cid}>",
                filename=str(inline_image.get("filename") or "image.png"),
                disposition="inline",
            )
    for attachment in attachments or []:
        content = attachment.get("content")
        if not content:
            continue
        maintype, subtype = parse_mime_type(
            str(attachment.get("mime_type") or "application/octet-stream")
        )
        message.add_attachment(
            content,
            maintype=maintype,
            subtype=subtype,
            filename=str(attachment.get("filename") or "attachment"),
        )
    return base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")


def parse_mime_type(value: str) -> tuple[str, str]:
    if "/" not in value:
        return "application", "octet-stream"
    maintype, subtype = value.split("/", 1)
    return maintype, subtype


def ensure_oauth_client_configured(settings: AppSettings) -> None:
    if not settings.google_client_id or not settings.google_client_secret:
        raise GmailServiceError(
            "Google OAuth client ID/secret are missing. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env.",
            status_code=400,
        )


def ensure_sender_configured(settings: AppSettings) -> None:
    ensure_oauth_client_configured(settings)
    if not settings.gmail_sender_email:
        raise GmailServiceError(
            "GMAIL_SENDER_EMAIL is missing. Set it to the Gmail account that completed OAuth.",
            status_code=400,
        )


def google_error_message(prefix: str, response: requests.Response) -> str:
    body = response.text.strip()
    if len(body) > 500:
        body = body[:500] + "..."
    return f"{prefix} with HTTP {response.status_code}: {body}"
