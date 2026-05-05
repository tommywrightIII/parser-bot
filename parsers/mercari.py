import asyncio
import logging
import os
import re
import traceback
from typing import Optional
from datetime import datetime
from dataclasses import dataclass
from playwright.async_api import async_playwright
from googletrans import Translator


@dataclass
class MercariItem:
    id: str
    name: str
    price: int
    condition: str
    size: Optional[str]
    image_url: str
    url: str
    seller: str
    status: str
    created_at: Optional[datetime] = None


COND_LABELS = {
    "1": "10/10 Новый",
    "2": "9/10 Почти новый",
    "3": "8/10 Хорошее",
    "4": "6/10 Среднее",
    "5": "4/10 Плохое",
}


def _extract_size_from_name(name: str) -> Optional[str]:
    patterns = [
        r'\b(\d{2,3}\.?\d?)\s*cm\b',
        r'\b(US\s*\d{1,2}\.?\d?)\b',
        r'\b(EU\s*\d{2,3})\b',
        r'\b(XS|S|M|L|XL|XXL|XXXL|FREE)\b',
        r'サイズ\s*:?\s*(\S+)',
        r'SIZE\s*:?\s*(\S+)',
        r'size\s*:?\s*(\S+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _parse_date(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts))
    except:
        return None


def _format_date(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    now = datetime.now()
    diff = now - dt
    minutes = int(diff.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes} мин. назад"
    elif minutes < 1440:
        hours = minutes // 60
        return f"{hours} ч. назад"
    else:
        days = minutes // 1440
        return f"{days} дн. назад"


def _is_japanese(text: str) -> bool:
    for ch in text:
        if '\u3040' <= ch <= '\u30ff' or '\u4e00' <= ch <= '\u9fff':
            return True
    return False


async def _translate_to_japanese(query: str) -> str:
    if _is_japanese(query):
        return query
    try:
        translator = Translator()
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: translator.translate(query, dest='ja')
        )
        translated = result.text
        logging.info(f"[Mercari] Перевод: {query} → {translated}")
        return translated
    except Exception as e:
        logging.warning(f"[Mercari] Ошибка перевода: {e}")
        return query


async def search_mercari(query, min_price=0, max_price=999999, condition=None, size=None, limit=10, proxy=None, category_id=None):
    results = []

    translated_query = await _translate_to_japanese(query)

    proxy_url = os.environ.get("PROXY_URL")
    logging.info(f"[Mercari] Запуск поиска: {query} → {translated_query}, прокси: {proxy_url}")

    proxy_config = None
    if proxy_url:
        match = re.match(r'(https?|socks5)://([^:@]+):([^@]+)@([^:]+):(\d+)', proxy_url)
        if match:
            proto, user, password, host, port = match.groups()
            proxy_config = {
                "server": f"{proto}://{host}:{port}",
                "username": user,
                "password": password,
            }
        else:
            proxy_config = {"server": proxy_url}

    try:
        async with async_playwright() as p:
            logging.info("[Mercari] Запускаем браузер...")
            browser = await p.chromium.launch(
                headless=True,
                proxy=proxy_config,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            logging.info("[Mercari] Браузер запущен")
            context = await browser.new_context(
                locale="ja-JP",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                extra_http_headers={
                    "Accept-Language": "ja-JP,ja;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
            )

            api_future = asyncio.get_event_loop().create_future()

            async def handle_response(response):
                if "entities:search" in response.url and response.status == 200:
                    try:
                        data = await response.json()
                        items = data.get("items", [])
                        logging.info(f"[Mercari] API ответил, items: {len(items)}")
                        if items and not api_future.done():
                            api_future.set_result(items)
                    except Exception as e:
                        logging.error(f"[Mercari] Ошибка парсинга API: {e}")
                elif "mercari" in response.url and response.status not in [200, 301, 302, 304]:
                    logging.info(f"[Mercari] Ответ {response.status}: {response.url[:80]}")

            page = await context.new_page()
            page.on("response", handle_response)

            url = f"https://jp.mercari.com/search?keyword={translated_query}&status=on_sale&sort=created_time&order=desc"
            if category_id:
                url += f"&categoryId={category_id}"
            if min_price > 0:
                url += f"&price_min={min_price}"
            if max_price < 999999:
                url += f"&price_max={max_price}"

            logging.info(f"[Mercari] Открываем: {url}")
            await page.goto(url, timeout=60000, wait_until="commit")
            logging.info("[Mercari] Страница загружена, ждём API...")

            try:
                items = await asyncio.wait_for(api_future, timeout=45)
                logging.info(f"[Mercari] Получено: {len(items)}")

                for item in items[:limit]:
                    thumbs = item.get("thumbnails", [])
                    name = item.get("name", "")
                    item_size = None
                    sizes = item.get("itemSizes", [])
                    if sizes:
                        item_size = sizes[0].get("name")
                    if not item_size:
                        item_size = _extract_size_from_name(name)
                    created_at = _parse_date(item.get("created", item.get("createdTime")))
                    results.append(MercariItem(
                        id=item.get("id", ""),
                        name=name,
                        price=int(item.get("price", 0)),
                        condition=COND_LABELS.get(str(item.get("itemConditionId", "")), "Не указано"),
                        size=item_size,
                        image_url=thumbs[0] if thumbs else "",
                        url=f"https://jp.mercari.com/item/{item.get('id', '')}",
                        seller=item.get("seller", {}).get("name", "") if isinstance(item.get("seller"), dict) else "",
                        status="В продаже",
                        created_at=created_at,
                    ))
            except asyncio.TimeoutError:
                logging.info("[Mercari] Таймаут ожидания API")

            await browser.close()

    except Exception as e:
        logging.error(f"[Mercari] Ошибка: {e}")
        logging.error(traceback.format_exc())

    logging.info(f"[Mercari] Найдено: {len(results)}")
    return results


def format_date(dt: Optional[datetime]) -> str:
    return _format_date(dt)
