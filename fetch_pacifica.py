import httpx
import asyncio
import os

API_KEY = os.getenv("PACIFICA_API_KEY", "")
if not API_KEY:
    raise ValueError("PACIFICA_API_KEY environment variable is not set")
BASE_URL = "https://api.pacifica.fi/api/v1"

async def fetch_markets(client):
    resp = await client.get("/crypto/markets")
    resp.raise_for_status()
    return resp.json()

async def fetch_price(client, symbol):
    resp = await client.get(f"/crypto/price/{symbol}")
    resp.raise_for_status()
    return resp.json()

async def main():
    async with httpx.AsyncClient(base_url=BASE_URL, headers={"X-API-KEY": API_KEY}) as client:
        markets = await fetch_markets(client)
        print("Available markets:")
        for m in markets:
            print(f"- {m}")
        # Example price for BTC
        try:
            price = await fetch_price(client, "BTC")
            print(f"\nBTC price: {price}")
        except Exception as e:
            print(f"Could not fetch BTC price: {e}")

if __name__ == "__main__":
    asyncio.run(main())
