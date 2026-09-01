import asyncio

import amazingdata_macos as ad


async def main():
    ad.login()
    async with ad.subscribe(["510300.SH"], period="snapshot") as stream:
        async for tick in stream:
            print(tick)


asyncio.run(main())
