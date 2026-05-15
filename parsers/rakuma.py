import asyncio
import logging
import os
import re
import json
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

def _find_items_in_data(data, depth=0):
    """Рекурсивно ищем массив с товарами в JSON"""
    if depth > 10:
        return None
    if isinstance(data, list) and len(data) > 0:
        first = data[0]
        if isinstance(first, dict) and any(k in first for k in ["id", "price", "name", "title"]):
            return data
    if isinstance(data, dict):
        for key, value in data.items():
            result = _find_items_in_data(value, depth + 1)
            if result:
                return result
    return None

async def search_rakuma(query, min_price=0, max_price=999999, condition=None, size=None, limit=10, proxy=None, category_id=None):
    results = []
    translated_query = await _translate_to_japanese(query)

    proxy_url = proxy or os.environ.get("PROXY_URL")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja-JP,ja;q=0.9",
        "Referer": "https://fril.jp/",
    }

    params = {
        "query": translated_query,
        "sort": "created_at",
        "order": "desc",
        "status": "on_sale",
    }
    if min_price > 0:
        params["price_min"] = min_price
    if max_price < 999999:
        params["price_max"] = max_price

    url = "https://fril.jp/s?" + urlencode(params, quote_via=quote)
    logging.info(f"[Rakuma] Запрос: {url}")

    try:
        if proxy_url and "socks5" in proxy_url:
            connector = ProxyConnector.from_url(proxy_url)
        else:
            connector = aiohttp.TCPConnector()

        async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                logging.info(f"[Rakuma] Статус: {resp.status}")
                html = await resp.text()
                logging.info(f"[Rakuma] HTML длина: {len(html)}")

                # Ищем __NEXT_DATA__ — Next.js вставляет все данные страницы сюда
                match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
                if match:
                    logging.info(f"[Rakuma] __NEXT_DATA__ найден!")
                    next_data = json.loads(match.group(1))

                    # Логируем структуру для отладки
                    def log_keys(d, prefix="", depth=0):
                        if depth > 4:
                            return
                        if isinstance(d, dict):
                            for k, v in d.items():
                                logging.info(f"[Rakuma] {prefix}{k}: {type(v).__name__}")
                                log_keys(v, prefix + "  ", depth + 1)
                    log_keys(next_data)

                    # Ищем товары рекурсивно
                    items_data = _find_items_in_data(next_data)
                    if items_data:
                        logging.info(f"[Rakuma] Найдено товаров: {len(items_data)}")
                        logging.info(f"[Rakuma] Пример товара: {json.dumps(items_data[0], ensure_ascii=False)[:500]}")
                    else:
                        logging.warning("[Rakuma] Товары в __NEXT_DATA__ не найдены")
                        # Логируем первые 2000 символов __NEXT_DATA__ для отладки
                        logging.info(f"[Rakuma] __NEXT_DATA__ начало: {match.group(1)[:2000]}")
                else:
                    logging.warning("[Rakuma] __NEXT_DATA__ не найден в HTML")
                    # Ищем другие JSON блоки
                    json_blocks = re.findall(r'<script[^>]*>(window\.__.*?)</script>', html, re.DOTALL)
                    for block in json_blocks[:3]:
                        logging.info(f"[Rakuma] JS блок: {block[:200]}")

                    # Логируем часть HTML где могут быть товары
                    idx = html.find("item")
                    if idx > 0:
                        logging.info(f"[Rakuma] HTML вокруг 'item': {html[max(0,idx-100):idx+500]}")

    except Exception as e:
        logging.error(f"[Rakuma] Ошибка: {e}")
        import traceback
        logging.error(traceback.format_exc())

    logging.info(f"[Rakuma] Найдено: {len(results)}")
    return results
