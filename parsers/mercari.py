import asyncio
import logging
import aiohttp
import re
from typing import Optional
from datetime import datetime
from dataclasses import dataclass


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


async def search_mercari(query, min_price=0, max_price=999999, condition=None, size=None, limit=10, proxy=None, category_id=None):
    results = []

    logging.info(f"[Mercari] Поиск: {query}")

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ja-JP,ja;q=0.9",
        "X-Platform": "web",
        "DPoP-Nonce": "",
    }

    params = {
        "keyword": query,
        "status": "on_sale",
        "sort": "created_time",
        "order": "desc",
        "limit": min(limit * 2, 100),
        "offset": 0,
    }

    if min_price > 0:
        params["price_min"] = min_price
    if max_price < 999999:
        params["price_max"] = max_price
    if category_id:
        params["category_id"] = category_id

    url = "https://api.mercari.jp/v2/entities:search"

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(url, json={
                "userId": "",
                "pageToken": "",
                "searchSessionId": "",
                "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
                "thumbnailTypes": [],
                "searchCondition": {
                    "keyword": query,
                    "excludeKeyword": "",
                    "sort": "SORT_CREATED_TIME",
                    "order": "ORDER_DESC",
                    "status": ["STATUS_ON_SALE"],
                    "categoryId": [category_id] if category_id else [],
                    "priceMin": min_price,
                    "priceMax": max_price if max_price < 999999 else 0,
                },
                "defaultDatasets": ["DATASET_TYPE_MERCARI"],
                "serviceFrom": "suruga",
                "withItemBrand": True,
                "withItemSize": True,
                "withItemPromotions": False,
                "withItemSizes": True,
                "fetchCartItems": False,
            }, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                logging.info(f"[Mercari] API статус: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    logging.info(f"[Mercari] Получено: {len(items)}")

                    for item in items[:limit]:
                        name = item.get("name", "")
                        item_size = None
                        sizes = item.get("itemSizes", [])
                        if sizes:
                            item_size = sizes[0].get("name")
                        if not item_size:
                            item_size = _extract_size_from_name(name)

                        thumbs = item.get("thumbnails", [])
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
                else:
                    text = await resp.text()
                    logging.error(f"[Mercari] Ошибка API: {resp.status} — {text[:200]}")

    except Exception as e:
        logging.error(f"[Mercari] Ошибка: {e}")

    logging.info(f"[Mercari] Найдено: {len(results)}")
    return results


def format_date(dt: Optional[datetime]) -> str:
    return _format_date(dt)
