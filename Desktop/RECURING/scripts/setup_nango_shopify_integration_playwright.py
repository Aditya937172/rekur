from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from dotenv import dotenv_values, load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


ROOT_DIR = Path(__file__).resolve().parents[1]
ROOT_ENV = ROOT_DIR / ".env"
NANGO_ENV = ROOT_DIR / "docker" / "nango" / ".env"
REPORT_PATH = ROOT_DIR / "data" / "nango_shopify_setup.json"
COMPOSE_FILE = ROOT_DIR / "docker" / "nango" / "docker-compose.yml"


class SetupError(RuntimeError):
    pass


def load_all_env() -> Dict[str, str]:
    load_dotenv(ROOT_ENV, override=True)
    root_values = {k: v for k, v in dotenv_values(ROOT_ENV).items() if v is not None}
    nango_values = {k: v for k, v in dotenv_values(NANGO_ENV).items() if v is not None}
    merged = {**root_values, **os.environ}
    merged["_NANGO_DOCKER_USERNAME"] = nango_values.get("NANGO_DASHBOARD_USERNAME", "")
    merged["_NANGO_DOCKER_PASSWORD"] = nango_values.get("NANGO_DASHBOARD_PASSWORD", "")
    merged["_NANGO_CONNECT_UI_PORT"] = nango_values.get("CONNECT_UI_PORT", "3010")
    return merged


def docker_available() -> bool:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip())
        return False
    return True


def ensure_retention_nango_running() -> str:
    if not docker_available():
        raise SetupError("Docker Desktop is not running. Start Docker Desktop and retry.")

    ps = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    required = {
        "retention-nango-server",
        "retention-nango-db",
        "retention-nango-redis",
    }
    if required.issubset(set(ps)):
        return "already_running"

    subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            "retention-nango",
            "-f",
            str(COMPOSE_FILE),
            "up",
            "-d",
        ],
        cwd=ROOT_DIR,
        check=True,
    )
    return "started"


def health_check(base_url: str) -> Dict[str, Any]:
    for path in ("/health", "/api/v1/health", "/"):
        try:
            response = httpx.get(f"{base_url.rstrip('/')}{path}", timeout=10)
        except httpx.HTTPError:
            continue
        if response.status_code < 500:
            return {"ok": response.status_code < 400, "status": response.status_code, "path": path}
    raise SetupError(f"Nango health check failed at {base_url}.")


def required_env(env: Dict[str, str], key: str, fallback: Optional[str] = None) -> str:
    value = env.get(key) or (env.get(fallback) if fallback else None)
    if not value:
        raise SetupError(f"{key} is required in .env.")
    return value


def verify_local_dashboard_user(email: str) -> None:
    escaped_email = email.replace("'", "''")
    subprocess.run(
        [
            "docker",
            "exec",
            "retention-nango-db",
            "psql",
            "-U",
            "nango",
            "-d",
            "nango",
            "-c",
            (
                "update nango._nango_users "
                "set email_verified = true, "
                "email_verification_token = null, "
                "email_verification_token_expires_at = null, "
                "updated_at = now() "
                f"where email = '{escaped_email}';"
            ),
        ],
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        check=False,
    )


def login_or_signup(page: Any, base_url: str, email: str, password: str) -> None:
    page.goto(f"{base_url}/signin", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector('input[name="email"]', timeout=30000)
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]', timeout=10000)
    try:
        page.wait_for_url(lambda url: "/signin" not in url, timeout=15000)
    except PlaywrightTimeoutError:
        pass

    if "/signin" not in page.url:
        return

    body = page.locator("body").inner_text(timeout=10000)
    if "Invalid email or password" not in body and "Don't have an account" not in body:
        raise SetupError("Could not log in to Nango dashboard.")

    page.goto(f"{base_url}/signup", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector('input[name="email"]', timeout=30000)
    page.fill('input[name="name"]', "Retention Admin")
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]', timeout=10000)
    page.wait_for_timeout(3000)
    verify_local_dashboard_user(email)

    page.goto(f"{base_url}/signin", wait_until="domcontentloaded", timeout=30000)
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]', timeout=10000)
    page.wait_for_url(lambda url: "/signin" not in url, timeout=20000)


def fetch_integration(page: Any, provider_config_key: str) -> Dict[str, Any]:
    return page.evaluate(
        """async ({key}) => {
            const r = await fetch(`/api/v1/integrations/${key}?env=dev`);
            return {status: r.status, text: await r.text()};
        }""",
        {"key": provider_config_key},
    )


def update_integration(
    page: Any,
    provider_config_key: str,
    client_id: str,
    client_secret: str,
    scopes: str,
) -> None:
    result = page.evaluate(
        """async ({key, clientId, clientSecret, scopes}) => {
            const r = await fetch(`/api/v1/integrations/${key}?env=dev`, {
                method: 'PATCH',
                headers: {'content-type': 'application/json'},
                body: JSON.stringify({
                    authType: 'OAUTH2',
                    clientId,
                    clientSecret,
                    scopes
                })
            });
            return {status: r.status, text: await r.text()};
        }""",
        {
            "key": provider_config_key,
            "clientId": client_id,
            "clientSecret": client_secret,
            "scopes": scopes,
        },
    )
    if result["status"] >= 400:
        raise SetupError(f"Nango integration update failed with HTTP {result['status']}.")


def create_integration_via_ui(
    page: Any,
    base_url: str,
    client_id: str,
    client_secret: str,
    scopes: str,
) -> None:
    page.goto(f"{base_url}/dev/integrations/create/shopify", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector('input[name="clientId"]', timeout=30000)
    page.fill('input[name="clientId"]', client_id)
    page.fill('input[name="clientSecret"]', client_secret)
    scope_input = page.locator('input[placeholder="Single scope or comma-separated list of scopes"]')
    scope_input.fill(scopes)
    page.keyboard.press("Enter")
    page.click('button[type="submit"]', timeout=10000)
    page.wait_for_url(lambda url: "/integrations/create" not in url, timeout=30000)


def get_redirect_url_from_settings(page: Any, base_url: str, provider_config_key: str) -> str:
    page.goto(
        f"{base_url}/dev/integrations/{provider_config_key}/settings",
        wait_until="domcontentloaded",
        timeout=30000,
    )
    page.wait_for_timeout(2000)
    inputs = page.locator("input").all()
    for input_el in inputs:
        value = input_el.input_value()
        if value.endswith("/oauth/callback"):
            return value
    return f"{base_url.rstrip('/')}/oauth/callback"


def write_report(
    provider_config_key: str,
    scopes: str,
    redirect_url: str,
    base_url: str,
    created_or_updated: bool,
) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "provider_config_key": provider_config_key,
                "scopes": scopes,
                "redirect_url": redirect_url,
                "nango_base_url": base_url,
                "created_or_updated": created_or_updated,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    env = load_all_env()
    base_url = env.get("NANGO_BASE_URL", "http://localhost:3005").rstrip("/")
    client_id = required_env(env, "SHOPIFY_CLIENT_ID", fallback="SHOPIFY_API_KEY")
    client_secret = required_env(env, "SHOPIFY_CLIENT_SECRET", fallback="SHOPIFY_API_SECRET")
    scopes = env.get("SHOPIFY_SCOPES", "read_products,read_customers,read_orders,read_inventory")
    provider_config_key = env.get("NANGO_PROVIDER_CONFIG_KEY", "shopify")
    dashboard_email = required_env(env, "_NANGO_DOCKER_USERNAME")
    dashboard_password = required_env(env, "_NANGO_DOCKER_PASSWORD")

    nango_status = ensure_retention_nango_running()
    health = health_check(base_url)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        login_or_signup(page, base_url, dashboard_email, dashboard_password)

        integration = fetch_integration(page, provider_config_key)
        created_or_updated = False
        if integration["status"] == 404:
            create_integration_via_ui(page, base_url, client_id, client_secret, scopes)
            created_or_updated = True
        elif integration["status"] == 200:
            update_integration(page, provider_config_key, client_id, client_secret, scopes)
            created_or_updated = True
        else:
            raise SetupError(
                f"Could not check integration {provider_config_key}: HTTP {integration['status']}."
            )

        latest = fetch_integration(page, provider_config_key)
        if latest["status"] != 200:
            raise SetupError("Integration did not exist after create/update.")
        data = json.loads(latest["text"])["data"]["integration"]
        if data.get("oauth_scopes") != scopes:
            update_integration(page, provider_config_key, client_id, client_secret, scopes)

        redirect_url = get_redirect_url_from_settings(page, base_url, provider_config_key)
        browser.close()

    write_report(provider_config_key, scopes, redirect_url, base_url, created_or_updated)
    print(f"NANGO_STATUS={nango_status}")
    print(f"NANGO_HEALTH={health['status']} {health['path']}")
    print("SHOPIFY_INTEGRATION_EXISTS=true")
    print(f"NANGO_PROVIDER_CONFIG_KEY={provider_config_key}")
    print(f"NANGO_SHOPIFY_REDIRECT_URL={redirect_url}")
    print(f"FILE_WRITTEN={REPORT_PATH}")


if __name__ == "__main__":
    try:
        main()
    except SetupError as exc:
        print(f"ERROR={exc}", file=sys.stderr)
        sys.exit(1)
