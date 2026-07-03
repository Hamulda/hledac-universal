from __future__ import annotations

import asyncio

from hledac.universal.utils.async_generators import async_chunked_pipeline


async def source():
    for i in range(100):
        yield i

async def processor(batch):
    print(f'Processing batch of {len(batch)} items')
    return [x * 2 for x in batch]

async def main():
    results = []
    count = 0
    async for batch_results in async_chunked_pipeline(source(), processor, batch_size=10):
        count += 1
        results.append(batch_results)
        if count > 15:
            break
    print(f'Total iterations: {count}')
    print(f'Result lengths: {[len(r) for r in results[:5]]}...')

asyncio.run(main())
