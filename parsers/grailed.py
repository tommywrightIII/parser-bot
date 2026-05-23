GRAILED_CATEGORIES = {
    "sneakers": {"name": "👟 Кроссовки", "query": "sneakers", "category": "sneakers"},
    "footwear": {"name": "👞 Обувь", "query": "footwear", "category": "footwear"},
    "tops": {"name": "👕 Верх", "query": "tops t-shirts", "category": "tops"},
    "outerwear": {"name": "🧥 Верхняя одежда", "query": "outerwear jackets", "category": "outerwear"},
    "bottoms": {"name": "👖 Низ", "query": "pants shorts", "category": "bottoms"},
    "accessories": {"name": "🎒 Аксессуары", "query": "accessories bags hats", "category": "accessories"},
    "streetwear": {"name": "🛹 Стритвир", "query": "supreme off-white bape", "category": None},
    "luxury": {"name": "💎 Люкс", "query": "gucci prada louis vuitton", "category": None},
}

import asyncio
import logging
import re
import urllib.parse
import random
from typing import Optional
from datetime import datetime
from dataclasses import dataclass
import aiohttp

@dataclass
class GrailedItem:
    id: str
    name: str
    price: int
    condition: str
    size: Optional[str]
    image_url: str
    url: str
    seller: str
    status: str
    brand: str = ""
    created_at: Optional[datetime] = None

def _extract_size(name: str) -> Optional[str]:
    patterns = [
        r'\b(XS|S|M|L|XL|XXL|XXXL)\b',
        r'\b(US\s*\d{1,2}\.?\d?)\b',
        r'\b(\d{2,3}\.?\d?)\s*cm\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

US_TO_EU = {
    "4": "36", "4.5": "36.5", "5": "37", "5.5": "37.5",
    "6": "38", "6.5": "38.5", "7": "40", "7.5": "40.5",
    "8": "41", "8.5": "42", "9": "42.5", "9.5": "43",
    "10": "44", "10.5": "44.5", "11": "45", "11.5": "45.5",
    "12": "46", "12.5": "47", "13": "47.5", "14": "48.5",
}

def _convert_size(size: str) -> str:
    if not size:
        return size
    if "EU" in str(size).upper():
        return size
    match = re.search(r'(\d+\.?\d*)', str(size))
    if match:
        num = match.group(1)
        eu = US_TO_EU.get(num)
        if eu:
            return f"US {num} / EU {eu}"
    return size

CONDITION_MAP = {
    "is_new": "10/10 Новый",
    "gently_used": "9/10 Почти новый",
    "used": "8/10 Хорошее",
    "worn": "6/10 Среднее",
}

async def search_grailed(query, min_price=0, max_price=999999, condition=None, size=None, limit=10, proxy=None, category_id=None):
    results = []

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.grailed.com",
        "Referer": "https://www.grailed.com/",
    }

    algolia_url = "https://mnrwefss2q-dsn.algolia.net/1/indexes/*/queries"
    algolia_params = {
        "x-algolia-agent": "Algolia for JavaScript (4.14.2); Browser (lite)",
        "x-algolia-api-key": "OTA4YWRiM2RiZTBkNjgzMmMwMTg2NDcyNWEzOTViMDg5NDQxZTc0NDQ0NzQ4MWI5ODAwNDAzODAwYjE3ZTQwNnZhbGlkVW50aWw9MTc3OTUyNjYwNiZ1c2VyVG9rZW49MTg3OTkwNzI=",
        "x-algolia-application-id": "MNRWEFSS2Q",
    }

    numeric_filters = []
    if min_price > 0:
        numeric_filters.append(f"price_i >= {min_price}")
    if max_price < 999999:
        numeric_filters.append(f"price_i <= {max_price}")

    facet_filters = []
    condition_map = {
        "new": "is_new",
        "like_new": "gently_used",
        "good": "used",
        "fair": "worn",
    }
    if condition and condition in condition_map:
        facet_filters.append([f"condition:{condition_map[condition]}"])

    params_dict = {
        "query": query,
        "hitsPerPage": min(limit, 40),
        "page": random.randint(0, 5),
        "distinct": True,
        "attributesToRetrieve": "id,title,price_i,condition,cover_photo,user,designer_names,size,created_at,slug",
    }
    if numeric_filters:
        params_dict["numericFilters"] = ",".join(numeric_filters)
    if facet_filters:
        params_dict["facetFilters"] = str(facet_filters)

    params_str = urllib.parse.urlencode(params_dict)

    payload = {
        "requests": [
            {
                "indexName": "Listing_production",
                "params": params_str,
            }
        ]
    }

    logging.info(f"[Grailed] Поиск: {query}, лимит: {limit}")

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(
                algolia_url,
                params=algolia_params,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                logging.info(f"[Grailed] Статус: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    hits = data.get("results", [{}])[0].get("hits", [])
                    logging.info(f"[Grailed] Найдено хитов: {len(hits)}")

                    for hit in hits[:limit]:
                        try:
                            item_id = str(hit.get("id", ""))
                            name = hit.get("title", "")
                            price = int(hit.get("price_i", 0))
                            brand = ", ".join(hit.get("designer_names", [])) if hit.get("designer_names") else ""
                            item_size = _convert_size(hit.get("size", "") or _extract_size(name) or "")
                            cond_raw = hit.get("condition", "")
                            condition_str = CONDITION_MAP.get(cond_raw, cond_raw)
                            slug = hit.get("slug", item_id)
                            item_url = f"https://www.grailed.com/listings/{slug}"

                            cover = hit.get("cover_photo", {})
                            image_url = ""
                            if isinstance(cover, dict):
                                image_url = cover.get("url", "")

                            user = hit.get("user", {})
                            seller = user.get("username", "") if isinstance(user, dict) else ""

                            if not name or price == 0:
                                continue
                            if min_price > 0 and price < min_price:
                                continue
                            if max_price < 999999 and price > max_price:
                                continue

                            created_at = None
                            if hit.get("created_at"):
                                try:
                                    created_at = datetime.fromisoformat(hit["created_at"].replace("Z", "+00:00"))
                                except Exception:
                                    pass

                            results.append(GrailedItem(
                                id=item_id,
                                name=name,
                                price=price,
                                condition=condition_str,
                                size=item_size,
                                image_url=image_url,
                                url=item_url,
                                seller=seller,
                                status="В продаже",
                                brand=brand,
                                created_at=created_at,
                            ))
                        except Exception as e:
                            logging.warning(f"[Grailed] Ошибка элемента: {e}")
                            continue
                else:
                    text = await resp.text()
                    logging.error(f"[Grailed] Ошибка {resp.status}: {text[:300]}")

    except Exception as e:
        logging.error(f"[Grailed] Ошибка: {e}")
        import traceback
        logging.error(traceback.format_exc())

    logging.info(f"[Grailed] Найдено: {len(results)}")
    return results
