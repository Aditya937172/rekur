from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import requests


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ShopifyAPIError(RuntimeError):
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
class ShopifyClient:
    store_domain: str
    admin_access_token: str
    api_version: str = "2026-01"
    timeout_seconds: int = 45
    max_retries: int = 5
    retry_base_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        self.store_domain = (
            self.store_domain.replace("https://", "")
            .replace("http://", "")
            .strip("/")
            .strip()
        )
        self.base_url = f"https://{self.store_domain}/admin/api/{self.api_version}"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Shopify-Access-Token": self.admin_access_token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "retention-app-seeder/1.0",
            }
        )

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        response = self._send(method, path_or_url, params=params, json=json)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ShopifyAPIError(
                "Shopify returned a non-JSON response.",
                status_code=response.status_code,
                response_body=response.text[:1000],
            ) from exc

    def graphql(
        self,
        query: str,
        *,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = self.request(
            "POST",
            "/graphql.json",
            json={"query": query, "variables": variables or {}},
        )
        if payload.get("errors"):
            raise ShopifyAPIError(
                "Shopify GraphQL request failed.",
                response_body=str(payload["errors"])[:2000],
            )
        return payload.get("data", {})

    def paginate(
        self,
        path: str,
        root_key: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> Iterable[Dict[str, Any]]:
        next_url: Optional[str] = None
        request_params = {"limit": 250, **(params or {})}

        while True:
            response = self._send(
                "GET",
                next_url or path,
                params=None if next_url else request_params,
            )
            payload = response.json()
            for item in payload.get(root_key, []):
                yield item

            next_url = self._extract_next_link(response.headers.get("Link"))
            if not next_url:
                break

    def create_product(self, product_payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("POST", "/products.json", json={"product": product_payload})

    def create_customer(self, customer_payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("POST", "/customers.json", json={"customer": customer_payload})

    def create_order(self, order_payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("POST", "/orders.json", json={"order": order_payload})

    def delete_product(self, product_id: int | str) -> None:
        self.request("DELETE", f"/products/{product_id}.json")

    def delete_customer(self, customer_id: int | str) -> None:
        self.request("DELETE", f"/customers/{customer_id}.json")

    def delete_order(self, order_id: int | str) -> None:
        self.request("DELETE", f"/orders/{order_id}.json")

    def list_seeded_products(self, seed_tag: str) -> List[Dict[str, Any]]:
        products = self.paginate(
            "/products.json",
            "products",
            params={"fields": "id,title,handle,tags,variants"},
        )
        return [product for product in products if self.has_tag(product, seed_tag)]

    def list_seeded_customers(self, seed_tag: str) -> List[Dict[str, Any]]:
        customers = self.paginate(
            "/customers/search.json",
            "customers",
            params={
                "query": f"tag:{seed_tag}",
                "fields": "id,email,phone,first_name,last_name,tags,note",
            },
        )
        return list(customers)

    def list_seeded_orders(self, seed_tag: str) -> List[Dict[str, Any]]:
        orders = self.paginate(
            "/orders.json",
            "orders",
            params={
                "status": "any",
                "fields": "id,name,tags,note,processed_at",
            },
        )
        return [order for order in orders if self.has_tag(order, seed_tag)]

    def graphql_seeded_products(self, seed_tag: str) -> List[Dict[str, Any]]:
        query = """
        query SeededProducts($cursor: String, $query: String!) {
          products(first: 250, after: $cursor, query: $query) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              legacyResourceId
              handle
              tags
              variants(first: 100) {
                nodes { id legacyResourceId sku }
              }
            }
          }
        }
        """
        nodes = self._graphql_collect(query, "products", f"tag:{seed_tag}")
        return [
            {
                "id": node.get("legacyResourceId") or self._legacy_id(node.get("id")),
                "handle": node.get("handle"),
                "tags": node.get("tags") or [],
                "variants": [
                    {
                        "id": variant.get("legacyResourceId")
                        or self._legacy_id(variant.get("id")),
                        "sku": variant.get("sku"),
                    }
                    for variant in (node.get("variants") or {}).get("nodes", [])
                ],
            }
            for node in nodes
        ]

    def graphql_seeded_customers(self, seed_tag: str) -> List[Dict[str, Any]]:
        query = """
        query SeededCustomers($cursor: String, $query: String!) {
          customers(first: 250, after: $cursor, query: $query) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              legacyResourceId
              tags
            }
          }
        }
        """
        nodes = self._graphql_collect(query, "customers", f"tag:{seed_tag}")
        return [
            {
                "id": node.get("legacyResourceId") or self._legacy_id(node.get("id")),
                "tags": node.get("tags") or [],
            }
            for node in nodes
        ]

    def graphql_seeded_orders(self, seed_tag: str) -> List[Dict[str, Any]]:
        query = """
        query SeededOrders($cursor: String, $query: String!) {
          orders(first: 250, after: $cursor, query: $query) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              legacyResourceId
              tags
            }
          }
        }
        """
        nodes = self._graphql_collect(query, "orders", f"tag:{seed_tag}")
        return [
            {
                "id": node.get("legacyResourceId") or self._legacy_id(node.get("id")),
                "tags": node.get("tags") or [],
            }
            for node in nodes
        ]

    @staticmethod
    def has_tag(resource: Dict[str, Any], tag: str) -> bool:
        tags = resource.get("tags") or ""
        if isinstance(tags, list):
            return tag in tags
        return tag in {part.strip() for part in str(tags).split(",") if part.strip()}

    def _send(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        url = (
            path_or_url
            if path_or_url.startswith("http")
            else f"{self.base_url}{path_or_url}"
        )
        last_response: Optional[requests.Response] = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                if method.upper() in {"POST", "PUT", "PATCH"}:
                    raise ShopifyAPIError(f"Shopify request failed: {exc}") from exc
                if attempt >= self.max_retries:
                    raise ShopifyAPIError(f"Shopify request failed: {exc}") from exc
                self._sleep_before_retry(attempt)
                continue

            last_response = response
            self._respect_bucket_header(response)

            if response.status_code in RETRYABLE_STATUS_CODES:
                if method.upper() in {"POST", "PUT", "PATCH"}:
                    break
                if attempt >= self.max_retries:
                    break
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    time.sleep(float(retry_after))
                else:
                    self._sleep_before_retry(attempt)
                continue

            if response.status_code >= 400:
                raise ShopifyAPIError(
                    f"Shopify {method} {path_or_url} failed with "
                    f"HTTP {response.status_code}.",
                    status_code=response.status_code,
                    response_body=response.text[:2000],
                )

            return response

        body = last_response.text[:2000] if last_response is not None else None
        status_code = last_response.status_code if last_response is not None else None
        raise ShopifyAPIError(
            f"Shopify {method} {path_or_url} failed after retries.",
            status_code=status_code,
            response_body=body,
        )

    def _sleep_before_retry(self, attempt: int) -> None:
        jitter = random.uniform(0, 0.35)
        delay = self.retry_base_delay_seconds * (2**attempt) + jitter
        time.sleep(delay)

    @staticmethod
    def _extract_next_link(link_header: Optional[str]) -> Optional[str]:
        if not link_header:
            return None
        for part in link_header.split(","):
            sections = [section.strip() for section in part.split(";")]
            if len(sections) < 2:
                continue
            url = sections[0].strip("<>")
            rel = ""
            for section in sections[1:]:
                if section.startswith("rel="):
                    rel = section.split("=", 1)[1].strip('"')
            if rel == "next":
                return url
        return None

    @staticmethod
    def _respect_bucket_header(response: requests.Response) -> None:
        header = response.headers.get("X-Shopify-Shop-Api-Call-Limit")
        if not header or "/" not in header:
            return
        try:
            used_text, bucket_text = header.split("/", 1)
            used = int(used_text)
            bucket = int(bucket_text)
        except ValueError:
            return

        if bucket > 0 and used / bucket >= 0.85:
            time.sleep(1.0)

    def _graphql_collect(
        self,
        query: str,
        root_key: str,
        search_query: str,
    ) -> List[Dict[str, Any]]:
        cursor: Optional[str] = None
        nodes: List[Dict[str, Any]] = []
        while True:
            data = self.graphql(
                query,
                variables={"cursor": cursor, "query": search_query},
            )
            connection = data[root_key]
            nodes.extend(connection.get("nodes", []))
            page_info = connection["pageInfo"]
            if not page_info["hasNextPage"]:
                return nodes
            cursor = page_info["endCursor"]

    @staticmethod
    def _legacy_id(graphql_id: Optional[str]) -> Optional[str]:
        if not graphql_id or "/" not in graphql_id:
            return graphql_id
        return graphql_id.rsplit("/", 1)[-1]
