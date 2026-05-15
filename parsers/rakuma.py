import asyncio
import logging
import os
import re
from typing import Optional
from datetime import datetime
from dataclasses import dataclass
from urllib.parse import urlencode, quote
import aiohttp
from aiohttp_socks import ProxyConnector
from deep_translator import GoogleTranslator

@dataclass
class RakumaItem:
    id: str
    name: str
    price: int
    condition: str
    size: Optional[str]
    image_url: str
    url: str
    seller: str
    status: str
    likes: int = 0
    created_at: Optional[datetime] = None

def _extract_size(name: str) -> Optional[str]:
    patterns = [
        r'\b(\d{2,3}\.?\d?)\s*cm\b',
        r'\b(US\s*\d{1,2}\.?\d?)\b',
        r'\b(XS|S|M|L|XL|XXL|XXXL|FREE)\b',
        r'サイズ\s*:?\s*(\S+)',
        r'SIZE\s*:?\s*(\S+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

def _is_japanese(text: str) -> bool:
    for ch in text:
        if '\u3040' <= ch <= '\u30ff' or '\u4e00' <= ch <= '\u9fff':
            return True
    return False

async def _translate_to_japanese(query: str) -> str:
    if _is_japanese(query):
        return query
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: GoogleTranslator(source='auto', target='ja').translate(query)
        )
        logging.info(f"[Rakuma] Перевод: {query} → {result}")
        return result
    except Exception as e:
        logging.warning(f"[Rakuma] Ошибка перевода: {e}")
        return query

async def search_rakuma(query, min_price=0, max_price=999999, condition=None, size=None, limit=10, proxy=None, category_id=None):
    results = []
    translated_query = await _translate_to_japanese(query)

    proxy_url = proxy or os.environ.get("PROXY_URL")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "ja-JP,ja;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://fril.jp/",
    }

    # Настраиваем прокси коннектор
    if proxy_url and "socks5" in proxy_url:
        connector = ProxyConnector.from_url(proxy_url)
    else:
        connector = aiohttp.TCPConnector()

    # Список возможных API endpoints для перебора
    endpoints = [
        "https://fril.jp/api/items/search",
        "https://fril.jp/api/v1/items/search",
        "https://fril.jp/api/v2/items/search",
        "https://fril.jp/api/search",
        "https://fril.jp/api/v1/search",
        "https://fril.jp/api/items",
    ]

    params = {
        "keyword": translated_query,
        "sort": "created_at",
        "order": "desc",
        "status": "on_sale",
        "page": 1,
        "per_page": limit,
    }
    if min_price > 0:
        params["price_min"] = min_price
    if max_price < 999999:
        params["price_max"] = max_price

    try:
        async with aiohttp.ClientSession(connector=connector, headers=headers) as session:

            # Сначала пробуем найти правильный endpoint через XHR перехват
            # Пробуем каждый endpoint
            for endpoint in endpoints:
                url = endpoint + "?" + urlencode(params, quote_via=quote)
                logging.info(f"[Rakuma] Пробуем: {url}")
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        logging.info(f"[Rakuma] {endpoint} → статус: {resp.status}, content-type: {resp.content_type}")
                        if resp.status == 200 and "json" in resp.content_type:
                            data = await resp.json(content_type=None)
                            logging.info(f"[Rakuma] Успех! Ключи ответа: {list(data.keys()) if isinstance(data, dict) else type(data)}")

                            items_data = []
                            if isinstance(data, dict):
                                items_data = data.get("items", data.get("results", data.get("data", [])))
                            elif isinstance(data, list):
                                items_data = data

                            logging.info(f"[Rakuma] Товаров в ответе: {len(items_data)}")

                            for item in items_data[:limit]:
                                try:
                                    item_id = str(item.get("id", ""))
                                    name = item.get("name", item.get("title", ""))
                                    price = int(item.get("price", 0))
                                    image_url = ""
                                    if item.get("thumbnails"):
                                        image_url = item["thumbnails"][0].get("url", "")
                                    else:
                                        image_url = item.get("image_url", item.get("thumbnail", ""))
                                    item_url = f"https://fril.jp/item/{item_id}"
                                    seller = item.get("seller", {}).get("name", "") if isinstance(item.get("seller"), dict) else ""
                                    condition = item.get("condition", {}).get("name", "") if isinstance(item.get("condition"), dict) else str(item.get("condition", ""))

                                    if not name or price == 0:
                                        continue
                                    if price < min_price or price > max_price:
                                        continue

                                    results.append(RakumaItem(
                                        id=item_id,
                                        name=name,
                                        price=price,
                                        condition=condition,
                                        size=_extract_size(name),
                                        image_url=image_url,
                                        url=item_url,
                                        seller=seller,
                                        status="on_sale",
                                    ))
                                except Exception as e:
                                    logging.warning(f"[Rakuma] Ошибка парсинга элемента: {e}")
                                    continue

                            if results:
                                break
                        else:
                            text = await resp.text()
                            logging.info(f"[Rakuma] {endpoint} → не подходит: {text[:100]}")
                except Exception as e:
                    logging.warning(f"[Rakuma] {endpoint} → ошибка: {e}")
                    continue

    except Exception as e:
        logging.error(f"[Rakuma] Ошибка: {e}")
        import traceback
        logging.error(traceback.format_exc())

    logging.info(f"[Rakuma] Найдено: {len(results)}")
    return results
