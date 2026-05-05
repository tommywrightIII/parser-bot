import asyncio
import logging
import re
import aiohttp
from typing import Optional
from datetime import datetime
from dataclasses import dataclass


@dataclass
class BunjangItem:
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


def _extract_size(name):
    for p in [r'\b(\d{2,3}\.?\d?)\s*cm\b', r'\b(US\s*\d{1,2}\.?\d?)\b', r'\b(XS|S|M|L|XL|XXL)\b']:
        m = re.search(p, name, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


async def search_bunjang(query, min_price=0, max_price=999999, condition=None, size=None, limit=10, proxy=None):
    results = []

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Accept": "application/json",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://m.bunjang.co.kr/",
        "Origin": "https://m.bunjang.co.kr",
    }

    params = {
        "q": query,
        "page": 0,
        "n": limit * 2,
        "order": "date",
    }

    if min_price > 0:
        params["min_price"] = min_price
    if max_price < 999999:
        params["max_price"] = max_price

    url = "https://api.bunjang.co.kr/api/1/find_v2.json"

    try:
        logging.info(f"[Bunjang] Поиск: {query}")
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                logging.info(f"[Bunjang] API статус: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("list", [])
                    logging.info(f"[Bunjang] Получено: {len(items)}")

                    for item in items[:limit]:
                        price = int(item.get("price", 0) or 0)
                        if min_price > 0 and price < min_price:
                            continue
                        if max_price < 999999 and price > max_price:
                            continue

                        item_id = str(item.get("pid", ""))
                        name = item.get("name", "")
                        img = item.get("product_image", "")
                        if img and not img.startswith("http"):
                            img = f"https://media.bunjang.co.kr/product/{img}"

                        results.append(BunjangItem(
                            id=item_id,
                            name=name,
                            price=price,
                            condition="Не указано",
                            size=_extract_size(name),
                            image_url=img,
                            url=f"https://m.bunjang.co.kr/products/{item_id}",
                            seller=item.get("seller_username", ""),
                            status="В продаже",
                        ))
                else:
                    text = await resp.text()
                    logging.error(f"[Bunjang] Ошибка: {resp.status} — {text[:200]}")

    except Exception as e:
        logging.error(f"[Bunjang] Ошибка: {e}")

    logging.info(f"[Bunjang] Найдено: {len(results)}")
    return results
