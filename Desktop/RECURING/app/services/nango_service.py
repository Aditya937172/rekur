from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from app.core.config import AppSettings, load_settings
from app.core.retry import ExternalAPIRetryError, httpx_request_with_retries


class NangoServiceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


@dataclass
class NangoService:
    base_url: str
    secret_key: Optional[str] = None
    environment: str = "dev"
    provider_config_key: str = "shopify"
    shopify_api_version: str = "2026-01"
    timeout_seconds: int = 30

    @classmethod
    def from_settings(cls, settings: Optional[AppSettings] = None) -> "NangoService":
        settings = settings or load_settings()
        return cls(
            base_url=settings.nango_base_url,
            secret_key=settings.nango_secret_key,
            environment=settings.nango_environment,
            provider_config_key=settings.nango_provider_config_key,
            shopify_api_version=settings.shopify_api_version,
            timeout_seconds=settings.nango_timeout_seconds,
        )

    def health_check(self) -> Dict[str, Any]:
        errors = []
        for path in ("/health", "/api/v1/health", "/"):
            try:
                with self._client() as client:
                    response = httpx_request_with_retries(
                        client,
                        "GET",
                        path,
                        provider="nango",
                        operation="health_check",
                    )
                if response.status_code < 500:
                    return {
                        "ok": response.status_code < 400,
                        "status_code": response.status_code,
                        "path": path,
                    }
                errors.append(f"{path}: HTTP {response.status_code}")
            except httpx.HTTPError as exc:
                errors.append(f"{path}: {exc}")
        raise NangoServiceError(
            "Nango health check failed.",
            response_body="; ".join(errors),
        )

    def list_integrations(self) -> Dict[str, Any]:
        return self._request("GET", "/api/v1/integrations")

    def get_connection(
        self,
        connection_id: str,
        provider_config_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        params = {}
        if provider_config_key:
            params["provider_config_key"] = provider_config_key
        return self._request(
            "GET",
            f"/api/v1/connection/{connection_id}",
            params=params,
        )

    def proxy_get(
        self,
        connection_id: str,
        provider_config_key: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_endpoint = endpoint.lstrip("/")
        proxy_params = {
            **(params or {}),
        }
        response = self._proxy_request(
            "GET",
            connection_id=connection_id,
            endpoint=clean_endpoint,
            params=proxy_params,
            provider_config_key=provider_config_key,
        )
        return self._json_response(response)

    def proxy_post(
        self,
        connection_id: str,
        provider_config_key: str,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        response = self._proxy_request(
            "POST",
            connection_id=connection_id,
            endpoint=endpoint.lstrip("/"),
            json=json,
            provider_config_key=provider_config_key,
        )
        return self._json_response(response)

    def proxy_get_legacy(
        self,
        connection_id: str,
        provider_config_key: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_endpoint = endpoint.lstrip("/")
        proxy_params = {
            "connection_id": connection_id,
            "provider_config_key": provider_config_key,
            **(params or {}),
        }
        return self._request(
            "GET",
            f"/api/v1/proxy/{clean_endpoint}",
            params=proxy_params,
        )

    def start_shopify_connection(
        self,
        end_user_id: str,
        *,
        provider_config_key: str = "shopify",
        organization_id: Optional[str] = None,
        success_url: Optional[str] = None,
        error_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "end_user": {"id": end_user_id},
            "allowed_integrations": [provider_config_key],
        }
        if organization_id:
            payload["organization"] = {"id": organization_id}
        # This self-hosted Nango image rejects success_url/error_url/callback_url
        # on /connect/sessions. The returned connect_link is enough to approve
        # or refresh a Shopify connection.
        return self._request("POST", "/connect/sessions", json=payload)

    def fetch_products(self, connection_id: str) -> list[Dict[str, Any]]:
        return self._fetch_shopify_collection(
            connection_id=connection_id,
            resource="products",
            root_key="products",
            params={
                "limit": 250,
                "fields": (
                    "id,title,body_html,handle,tags,variants,image,images,"
                    "created_at,updated_at"
                ),
            },
        )

    def fetch_customers(self, connection_id: str) -> list[Dict[str, Any]]:
        return self._fetch_shopify_collection(
            connection_id=connection_id,
            resource="customers",
            root_key="customers",
            params={
                "limit": 250,
                "fields": (
                    "id,first_name,last_name,email,phone,addresses,default_address,"
                    "orders_count,total_spent,created_at,updated_at,last_order_id"
                ),
            },
        )

    def fetch_orders(self, connection_id: str) -> list[Dict[str, Any]]:
        return self._fetch_shopify_collection(
            connection_id=connection_id,
            resource="orders",
            root_key="orders",
            params={
                "limit": 250,
                "status": "any",
                "fields": (
                    "id,name,customer,line_items,total_price,currency,"
                    "created_at,updated_at,financial_status,fulfillment_status"
                ),
            },
        )

    def _fetch_shopify_collection(
        self,
        *,
        connection_id: str,
        resource: str,
        root_key: str,
        params: Dict[str, Any],
    ) -> list[Dict[str, Any]]:
        records: list[Dict[str, Any]] = []
        next_params: Optional[Dict[str, Any]] = params.copy()

        while next_params is not None:
            response = self._proxy_request(
                "GET",
                connection_id=connection_id,
                endpoint=f"admin/api/{self.shopify_api_version}/{resource}.json",
                params=next_params,
            )
            payload = self._json_response(response)
            page_records = payload.get(root_key, [])
            if not isinstance(page_records, list):
                raise NangoServiceError(
                    f"Shopify {resource} response did not contain a list at '{root_key}'.",
                    status_code=response.status_code,
                    response_body=response.text[:1000],
                )
            records.extend(page_records)
            next_params = self._next_page_params(response.headers.get("link"))

        return records

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.secret_key:
            raise NangoServiceError(
                "NANGO_SECRET_KEY is required for authenticated Nango API calls."
            )

        try:
            with self._client() as client:
                response = httpx_request_with_retries(
                    client,
                    method,
                    path,
                    provider="nango",
                    operation=path.strip("/") or "root",
                    params=params,
                    json=json,
                    headers=self._auth_headers(),
                )
        except (httpx.HTTPError, ExternalAPIRetryError) as exc:
            raise NangoServiceError(
                f"Nango {method} {path} failed after retries: {exc}",
                response_body=str(exc),
            ) from exc
        if response.status_code >= 400:
            raise NangoServiceError(
                f"Nango {method} {path} failed with HTTP {response.status_code}.",
                status_code=response.status_code,
                response_body=response.text[:2000],
            )

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise NangoServiceError(
                "Nango returned a non-JSON response.",
                status_code=response.status_code,
                response_body=response.text[:1000],
            ) from exc

    def _proxy_request(
        self,
        method: str,
        *,
        connection_id: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        provider_config_key: Optional[str] = None,
    ) -> httpx.Response:
        if not self.secret_key:
            raise NangoServiceError(
                "NANGO_SECRET_KEY is required for Shopify sync through Nango."
            )

        try:
            with self._client() as client:
                response = httpx_request_with_retries(
                    client,
                    method,
                    f"/proxy/{endpoint.lstrip('/')}",
                    provider="nango_proxy",
                    operation=endpoint.lstrip("/"),
                    params=params,
                    json=json,
                    headers={
                        **self._auth_headers(),
                        "Provider-Config-Key": provider_config_key or self.provider_config_key,
                        "Connection-Id": connection_id,
                    },
                )
        except (httpx.HTTPError, ExternalAPIRetryError) as exc:
            raise NangoServiceError(
                f"Nango proxy {method} {endpoint} failed after retries: {exc}",
                response_body=str(exc),
            ) from exc
        if response.status_code >= 400:
            raise NangoServiceError(
                f"Nango proxy {method} {endpoint} failed with HTTP {response.status_code}.",
                status_code=response.status_code,
                response_body=response.text[:2000],
            )
        return response

    def _json_response(self, response: httpx.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise NangoServiceError(
                "Nango proxy returned a non-JSON response.",
                status_code=response.status_code,
                response_body=response.text[:1000],
            ) from exc
        if not isinstance(payload, dict):
            raise NangoServiceError(
                "Nango proxy returned an unexpected JSON response.",
                status_code=response.status_code,
                response_body=response.text[:1000],
            )
        return payload

    def _next_page_params(self, link_header: Optional[str]) -> Optional[Dict[str, Any]]:
        if not link_header:
            return None
        for part in link_header.split(","):
            section = part.strip()
            if 'rel="next"' not in section:
                continue
            start = section.find("<")
            end = section.find(">")
            if start == -1 or end == -1 or end <= start:
                continue
            next_url = section[start + 1 : end]
            query = parse_qs(urlparse(next_url).query)
            return {key: values[-1] for key, values in query.items() if values}
        return None

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url.rstrip("/"),
            timeout=self.timeout_seconds,
            follow_redirects=True,
        )

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Nango-Environment": self.environment,
            "X-Nango-Environment": self.environment,
        }
