from fastapi import FastAPI, HTTPException, Depends
import httpx
import os
from typing import Any, Dict

app = FastAPI(title="Pacifica Crypto Market Info")

# API key provided by the user - load from environment variable
PACIFICA_API_KEY = os.getenv("PACIFICA_API_KEY", "")
if not PACIFICA_API_KEY:
    raise ValueError("PACIFICA_API_KEY environment variable is not set")

# Base URLs (mainnet and testnet)
MAINNET_BASE_URL = "https://api.pacifica.fi/api/v1"
TESTNET_BASE_URL = "https://test-api.pacifica.fi/api/v1"

# Choose which network to use; default to mainnet
USE_TESTNET = os.getenv("USE_TESTNET", "false").lower() == "true"
BASE_URL = TESTNET_BASE_URL if USE_TESTNET else MAINNET_BASE_URL

async def get_pacifica_client() -> httpx.AsyncClient:
    """Dependency that provides an httpx.AsyncClient with the API key header."""
    headers = {
        "Authorization": f"Bearer {PACIFICA_API_KEY}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=10.0) as client:
        yield client

@app.get("/crypto/markets", summary="Get list of available crypto markets")
async def get_markets(client: httpx.AsyncClient = Depends(get_pacifica_client)):
    """
    Fetches the list of markets from the Pacifica REST API.
    """
    try:
        response = await client.get("/markets")
        response.raise_for_status()
        data: Any = response.json()
        return data
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code,
                            detail=f"Pacifica API error: {exc.response.text}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502,
                            detail=f"Request to Pacifica failed: {str(exc)}")

@app.get("/crypto/price/{symbol}", summary="Get price for a specific crypto symbol")
async def get_price(symbol: str, client: httpx.AsyncClient = Depends(get_pacifica_client)):
    """
    Fetches the ticker/price for a given symbol (e.g., BTC, ETH).
    Assumes the Pacifica API provides a ticker endpoint like `/ticker/{symbol}`.
    """
    try:
        response = await client.get(f"/ticker/{symbol.upper()}")
        response.raise_for_status()
        data: Any = response.json()
        return data
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code,
                            detail=f"Pacifica API error: {exc.response.text}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502,
                            detail=f"Request to Pacifica failed: {str(exc)}")

# Optional health check endpoint
@app.get("/health", summary="Health check")
async def health_check():
    return {"status": "ok", "network": "testnet" if USE_TESTNET else "mainnet"}
