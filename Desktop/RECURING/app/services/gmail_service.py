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


def list_gmail_messages(
    *,
    query: str = "",
    max_results: int = 50,
    settings: AppSettings | None = None,
) -> list[dict]:
    settings = settings or load_settings()
    access_token = refresh_access_token(settings=settings)

    response = requests.get(
        f"{settings.gmail_api_base_url}/users/me/messages",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"q": query, "maxResults": max_results},
        timeout=settings.gmail_timeout_seconds,
    )

    if response.status_code >= 400:
        raise GmailServiceError(
            google_error_message("Failed to list Gmail messages", response),
            status_code=502,
        )

    return response.json().get("messages", [])


def get_gmail_message(
    message_id: str,
    *,
    settings: AppSettings | None = None,
) -> dict:
    settings = settings or load_settings()
    access_token = refresh_access_token(settings=settings)

    response = requests.get(
        f"{settings.gmail_api_base_url}/users/me/messages/{message_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"format": "full"},
        timeout=settings.gmail_timeout_seconds,
    )

    if response.status_code >= 400:
        raise GmailServiceError(
            google_error_message("Failed to get Gmail message", response),
            status_code=502,
        )

    return response.json()


def decode_gmail_body(payload: dict) -> str:
    body_data = ""

    if "body" in payload and "data" in payload["body"]:
        body_data = payload["body"]["data"]
    elif "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
                body_data = part["body"]["data"]
                break
            elif part.get("mimeType") == "text/html" and "data" in part.get("body", {}):
                body_data = part["body"]["data"]

    if body_data:
        return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
    return ""


def extract_email_headers(message: dict) -> dict:
    headers = message.get("payload", {}).get("headers", [])
    header_dict = {h["name"]: h["value"] for h in headers}

    from_header = header_dict.get("From", "")
    if "<" in from_header and ">" in from_header:
        from_email = from_header.split("<")[1].split(">")[0]
    else:
        from_email = from_header

    return {
        "message_id": message.get("id"),
        "thread_id": message.get("threadId"),
        "from_email": from_email.strip().lower(),
        "from_name": from_header.split("<")[0].strip()
        if "<" in from_header
        else from_header,
        "subject": header_dict.get("Subject", ""),
        "date": header_dict.get("Date", ""),
    }


def list_recent_replies(
    *,
    after_timestamp: int | None = None,
    sender_domain: str | None = None,
    max_results: int = 50,
    settings: AppSettings | None = None,
) -> list[dict]:
    settings = settings or load_settings()

    query_parts = ["in:inbox"]
    query_parts.append("is:unread")

    if after_timestamp:
        query_parts.append(f"after:{after_timestamp}")

    query = " ".join(query_parts)

    messages = list_gmail_messages(
        query=query, max_results=max_results, settings=settings
    )

    replies = []
    for msg_summary in messages:
        try:
            message = get_gmail_message(msg_summary["id"], settings=settings)
            headers = extract_email_headers(message)
            body = decode_gmail_body(message.get("payload", {}))

            if body and len(body.strip()) > 5:
                replies.append(
                    {
                        "message_id": headers["message_id"],
                        "thread_id": headers["thread_id"],
                        "from_email": headers["from_email"],
                        "from_name": headers["from_name"],
                        "subject": headers["subject"],
                        "body": body.strip()[:2000],
                    }
                )
        except Exception as e:
            logger.error(f"Failed to process message {msg_summary['id']}: {e}")

    return replies


def mark_message_as_read(
    message_id: str,
    *,
    settings: AppSettings | None = None,
) -> bool:
    settings = settings or load_settings()
    access_token = refresh_access_token(settings=settings)

    response = requests.post(
        f"{settings.gmail_api_base_url}/users/me/messages/{message_id}/modify",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"removeLabelIds": ["UNREAD"]},
        timeout=settings.gmail_timeout_seconds,
    )

    return response.status_code < 400
