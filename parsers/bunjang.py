import asyncio
import logging
import re
import aiohttp
from typing import Optional
from datetime import datetime
from dataclasses import dataclass
from googletrans import Translator


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


BUNJANG_CONDITIONS = {
    "S": "10/10 Новый",
    "A": "9/10 Почти новый",
    "B": "8/10 Хорошее",
    "C": "6/10 Среднее",
    "D": "4/10 Плохое",
}

BUNJANG_CATEGORIES = {
    "shoes": {"name": "👟 Обувь", "id": "310"},
    "clothes": {"name": "👕 Одежда", "id": "300"},
    "bags": {"name": "👜 Сумки", "id": "320"},
    "hats": {"name": "🧢 Головные уборы", "id": "321"},
    "watches": {"name": "⌚ Часы", "id": "331"},
    "accessories": {"name": "💍 Аксессуары", "id": "330"},
}


def _extract_size(name):
    for p in [r'\b(\d{2,3}\.?\d?)\s*cm\b', r'\b(US\s*\d{1,2}\.?\d?)\b', r'\b(XS|S|M|L|XL|XXL)\b']:
        m = re.search(p, name, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _is_korean(text: str) -> bool:
    for ch in text:
        if '\uac00' <= ch <= '\ud7a3':
            return True
    return False


async def _translate(text: str, dest: str) -> str:
    try:
        translator = Translator()
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: translator.translate(text, dest=dest)
        )
        return result.text
    except Exception as e:
        logging.warning(f"[Bunjang] Ошибка перевода: {e}")
        return text


async def _get_item_details(session: aiohttp.ClientSession, item_id: str) -> dict:
    url = f"https://api.bunjang.co.kr/api/1/product/{item_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Accept": "application/json",
    }
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                product = data.get("product", {})
                return {
                    "condition": product.get("condition", ""),
                    "size": product.get("productSize", {}).get("name", "") if product.get("productSize") else "",
                }
    except Exception as e:
        logging.warning(f"[Bunjang] Ошибка деталей {item_id}: {e}")
    return {"condition": "", "size": ""}


async def search_bunjang(query, min_price=0, max_price=999999, condition=None, size=None, limit=10, proxy=None, category_id=None):
    results = []

    translated_query = query
    if not _is_korean(query):
        translated_query = await _translate(query, 'ko')
        logging.info(f"[Bunjang] Перевод запроса: {query} → {translated_query}")

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Accept": "application/json",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://m.bunjang.co.kr/",
        "Origin": "https://m.bunjang.co.kr",
    }

    params = {
        "q": translated_query,
        "page": 0,
        "n": limit * 2,
        "order": "date",
    }

    if category_id:
        params["category_id"] = category_id
    if min_price > 0:
        params["min_price"] = min_price
    if max_price < 999999:
        params["max_price"] = max_price

    url = "https://api.bunjang.co.kr/api/1/find_v2.json"

    try:
        logging.info(f"[Bunjang] Поиск: {query} → {translated_query}")
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                logging.info(f"[Bunjang] API статус: {resp.status}")
                if resp.status != 200:
                    text = await resp.text()
                    logging.error(f"[Bunjang] Ошибка: {resp.status} — {text[:200]}")
                    return results

                data = await resp.json()
                items = data.get("list", [])
                logging.info(f"[Bunjang] Получено: {len(items)}")

                items = items[:limit]

                # Параллельно получаем детали и переводим названия
                detail_tasks = [_get_item_details(session, str(item.get("pid", ""))) for item in items]
                translate_tasks = [_translate(item.get("name", ""), 'en') for item in items]

                details_list, translated_names = await asyncio.gather(
                    asyncio.gather(*detail_tasks),
                    asyncio.gather(*translate_tasks)
                )

                for item, details, translated_name in zip(items, details_list, translated_names):
                    price = int(item.get("price", 0) or 0)
                    if min_price > 0 and price < min_price:
                        continue
                    if max_price < 999999 and price > max_price:
                        continue

                    item_id = str(item.get("pid", ""))
                    original_name = item.get("name", "")
                    display_name = f"{translated_name}\n<i>{original_name}</i>" if translated_name != original_name else original_name

                    img = item.get("product_image", "")
                    if img and not img.startswith("http"):
                        img = f"https://media.bunjang.co.kr/product/{img}"

                    item_condition = BUNJANG_CONDITIONS.get(details.get("condition", ""), "Не указано")
                    item_size = details.get("size", "") or _extract_size(original_name)

                    results.append(BunjangItem(
                        id=item_id,
                        name=display_name,
                        price=price,
                        condition=item_condition,
                        size=item_size if item_size else None,
                        image_url=img,
                        url=f"https://m.bunjang.co.kr/products/{item_id}",
                        seller=item.get("seller_username", ""),
                        status="В продаже",
                    ))

    except Exception as e:
        logging.error(f"[Bunjang] Ошибка: {e}")
        import traceback
        logging.error(traceback.format_exc())

    logging.info(f"[Bunjang] Найдено: {len(results)}")
    return results
