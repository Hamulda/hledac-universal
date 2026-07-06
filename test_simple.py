#!/usr/bin/env python3
"""Quick test script."""
print("Starting test async handler...")
import sys
sys.stdout.flush()

try:
    from runtime.observability_async_handler import AsyncLogHandler, configure_async_logging
    print("observability_async_handler imported OK")
    sys.stdout.flush()

    import asyncio
    async def test():
        handler = await AsyncLogHandler.get_instance()
        print("get_instance OK")
        await handler.start()
        print("start OK")
        await handler.emit("test message")
        print("emit OK")
        await handler.stop()
        print("stop OK")

    asyncio.run(test())
    print("async test OK")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("Done")
