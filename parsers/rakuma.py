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

    # Мобильный API Rakuma
    endpoints = [
        {
            "url": "https://api.fril.jp/v1/items/search",
            "headers": {
                "User-Agent": "Rakuma/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
                "Accept": "application/json",
                "Accept-Language": "ja-JP",
                "X-Api-Version": "5",
            },
            "params": {
                "keyword": translated_query,
                "sort": "created_at",
                "order": "desc",
                "status": "on_sale",
                "page": 1,
                "per_page": limit,
            }
        },
        {
            "url": "https://api.fril.jp/v2/items/search",
            "headers": {
                "User-Agent": "Rakuma/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
                "Accept": "application/json",
                "Accept-Language": "ja-JP",
            },
            "params": {
                "keyword": translated_query,
                "sort": "created_at",
                "order": "desc",
                "status": "on_sale",
                "page": 1,
                "per_page": limit,
            }
        },
        {
            "url": "https://api.fril.jp/v1/search",
            "headers": {
                "User-Agent": "Rakuma/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
                "Accept": "application/json",
                "Accept-Language": "ja-JP",
            },
            "params": {
                "q": translated_query,
                "sort": "created_at",
                "order": "desc",
                "page": 1,
            }
        },
        {
            "url": "https://rakuma.rakuten.co.jp/api/v1/items/search",
            "headers": {
                "User-Agent": "Rakuma/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
                "Accept": "application/json",
                "Accept-Language": "ja-JP",
            },
            "params": {
                "keyword": translated_query,
                "sort": "created_at",
                "order": "desc",
                "status": "on_sale",
                "page": 1,
                "per_page": limit,
            }
        },
    ]

    try:
        if proxy_url and "socks5" in proxy_url:
            connector = ProxyConnector.from_url(proxy_url)
        else:
            connector = aiohttp.TCPConnector()

        async with aiohttp.ClientSession(connector=connector) as session:
            for ep in endpoints:
                url = ep["url"] + "?" + urlencode(ep["params"], quote_via=quote)
                logging.info(f"[Rakuma] Пробуем: {url[:80]}")
                try:
                    async with session.get(url, headers=ep["headers"], timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        logging.info(f"[Rakuma] Статус: {resp.status}, CT: {resp.content_type}")
                        text = await resp.text()
                        logging.info(f"[Rakuma] Ответ: {text[:300]}")

                        if resp.status == 200 and "json" in resp.content_type:
                            import json
                            data = json.loads(text)
                            logging.info(f"[Rakuma] JSON ключи: {list(data.keys()) if isinstance(data, dict) else type(data)}")

                            items_data = []
                            if isinstance(data, dict):
                                items_data = data.get("items", data.get("results", data.get("data", [])))
                            elif isinstance(data, list):
                                items_data = data

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
                                    cond = item.get("condition", {}).get("name", "") if isinstance(item.get("condition"), dict) else str(item.get("condition", ""))

                                    if not name or price == 0:
                                        continue
                                    if price < min_price or price > max_price:
                                        continue

                                    results.append(RakumaItem(
                                        id=item_id,
                                        name=name,
                                        price=price,
                                        condition=cond,
                                        size=_extract_size(name),
                                        image_url=image_url,
                                        url=item_url,
                                        seller=seller,
                                        status="on_sale",
                                    ))
                                except Exception as e:
                                    logging.warning(f"[Rakuma] Ошибка элемента: {e}")

                            if results:
                                logging.info(f"[Rakuma] Успех через {ep['url']}")
                                break

                except Exception as e:
                    logging.warning(f"[Rakuma] Ошибка {ep['url']}: {e}")
                    continue

    except Exception as e:
        logging.error(f"[Rakuma] Ошибка: {e}")
        import traceback
        logging.error(traceback.format_exc())

    logging.info(f"[Rakuma] Найдено: {len(results)}")
    return results
