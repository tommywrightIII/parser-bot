import asyncio
import logging
import httpx
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from handlers.search import router as search_router
from handlers.track import router as track_router, tracking_loop
from config import BOT_TOKEN

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(search_router)
dp.include_router(track_router)


async def main():
    logging.info("🤖 Бот запущен")
    try:
        r = httpx.get("https://api.ipify.org")
        logging.info(f"Railway IP: {r.text}")
    except Exception as e:
        logging.error(f"IP error: {e}")
    asyncio.create_task(tracking_loop(bot))
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
