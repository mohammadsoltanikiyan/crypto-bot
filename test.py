import aiohttp
import asyncio

async def test():
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT") as r:
            print(r.status)
            print(await r.json())

asyncio.run(test())
