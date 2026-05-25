import aiohttp
import asyncio
import logging
import re
import json
from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class SecondStreetItem:
    title: str
    price: int
    currency: str
    url: str
    image_url: Optional[str]
    brand: str
    size: Optional[str]
    condition: Optional[str]
    goods_id: str


async def search_secondstreet(
    keyword: str,
    session: aiohttp.ClientSession = None,
    proxy: Optional[str] = None,
    max_items: int = 20,
    sort_by: str = "arrival",
) -> list:
    import os
    # Японский прокси для 2ndstreet.jp
    proxy = os.environ.get("PROXY_URL_JP", proxy)
    from aiohttp_socks import ProxyConnector

    url = "https://www.2ndstreet.jp/search"
    params = {
        "keyword": keyword,
        "sortBy": sort_by,
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    }

    logging.info(f"[2ndStreet] Поиск: {keyword}, proxy: {proxy}")

    try:
        if proxy and "socks5" in proxy:
            connector = ProxyConnector.from_url(proxy, ssl=False)
        else:
            connector = aiohttp.TCPConnector(ssl=False)

        async with aiohttp.ClientSession(connector=connector, headers=headers) as sess:
            # Сначала заходим на главную чтобы получить куки
            try:
                async with sess.get("https://www.2ndstreet.jp/", timeout=aiohttp.ClientTimeout(total=15)) as r:
                    logging.info(f"[2ndStreet] Главная: {r.status}, куки: {len(sess.cookie_jar)}")
            except Exception as e:
                logging.warning(f"[2ndStreet] Ошибка главной: {e}")

            await asyncio.sleep(1)

            async with sess.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                logging.info(f"[2ndStreet] Статус: {resp.status}")
                if resp.status != 200:
                    text_err = await resp.text()
                    logging.error(f"[2ndStreet] HTTP {resp.status}: {text_err[:200]}")
                    return []
                html = await resp.text()
                logging.info(f"[2ndStreet] HTML длина: {len(html)}")

    except Exception as e:
        logging.error(f"[2ndStreet] Ошибка запроса: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return []

    items = []
    seen_ids = set()

    # Метод 1: ищем JSON данные товаров в __NEXT_DATA__ или dataLayer
    next_data_match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if next_data_match:
        logging.info("[2ndStreet] Найден __NEXT_DATA__")
        try:
            data = json.loads(next_data_match.group(1))
            # Рекурсивно ищем товары
            items_data = _find_items(data)
            if items_data:
                logging.info(f"[2ndStreet] Товаров в JSON: {len(items_data)}")
                for item in items_data[:max_items]:
                    try:
                        goods_id = str(item.get("goodsId", item.get("id", "")))
                        if not goods_id or goods_id in seen_ids:
                            continue
                        seen_ids.add(goods_id)
                        price = int(item.get("price", item.get("sellPrice", 0)))
                        title = item.get("goodsName", item.get("name", item.get("title", "")))
                        brand = item.get("brandName", item.get("brand", ""))
                        image_url = item.get("imagePath", item.get("imageUrl", item.get("image", "")))
                        if image_url and image_url.startswith("//"):
                            image_url = "https:" + image_url
                        size = item.get("size", item.get("sizeName", ""))
                        condition = item.get("goodsStatus", item.get("condition", ""))
                        item_url = f"https://www.2ndstreet.jp/goods/detail/goodsId/{goods_id}"

                        items.append(SecondStreetItem(
                            title=title,
                            price=price,
                            currency="¥",
                            url=item_url,
                            image_url=image_url,
                            brand=brand,
                            size=size,
                            condition=condition,
                            goods_id=goods_id,
                        ))
                    except Exception as e:
                        logging.warning(f"[2ndStreet] Ошибка элемента JSON: {e}")
        except Exception as e:
            logging.warning(f"[2ndStreet] Ошибка парсинга __NEXT_DATA__: {e}")

    # Метод 2: парсим HTML карточки
    if not items:
        logging.info("[2ndStreet] Пробуем HTML парсинг")
        card_pattern = re.compile(
            r'goodsid="(\d+)".*?'
            r'href="(/goods/detail/[^"]+)".*?'
            r'<img[^>]+src="([^"]+)".*?'
            r'(?:itemCard_brand[^>]*>([^<]*)</p>)?.*?'
            r'(?:itemCard_name[^>]*>([^<]*)</p>)?.*?'
            r'(?:itemCard_size[^>]*>([^<]*)</p>)?.*?'
            r'¥([\d,]+)',
            re.DOTALL
        )
        for m in card_pattern.finditer(html):
            goods_id = m.group(1)
            if goods_id in seen_ids:
                continue
            seen_ids.add(goods_id)
            try:
                price = int(m.group(7).replace(",", ""))
                items.append(SecondStreetItem(
                    title=f"{(m.group(4) or '').strip()} {(m.group(5) or '').strip()}".strip(),
                    price=price,
                    currency="¥",
                    url=f"https://www.2ndstreet.jp{m.group(2)}",
                    image_url=m.group(3),
                    brand=(m.group(4) or "").strip(),
                    size=(m.group(6) or "").strip() or None,
                    condition=None,
                    goods_id=goods_id,
                ))
                if len(items) >= max_items:
                    break
            except Exception as e:
                logging.warning(f"[2ndStreet] Ошибка HTML элемента: {e}")

    # Логируем часть HTML для отладки если ничего не нашли
    if not items:
        logging.warning(f"[2ndStreet] Ничего не найдено. HTML начало: {html[:1000]}")

    logging.info(f"[2ndStreet] Найдено: {len(items)}")
    return items


def _find_items(data, depth=0):
    if depth > 8:
        return None
    if isinstance(data, list) and len(data) > 0:
        first = data[0]
        if isinstance(first, dict) and any(k in first for k in ["goodsId", "goodsName", "sellPrice", "price"]):
            return data
    if isinstance(data, dict):
        for key, value in data.items():
            result = _find_items(value, depth + 1)
            if result:
                return result
    return None
