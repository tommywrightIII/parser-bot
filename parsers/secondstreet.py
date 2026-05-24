import aiohttp
import asyncio
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
    session: aiohttp.ClientSession,
    proxy: Optional[str] = None,
    max_items: int = 20,
    sort_by: str = "arrival",  # arrival = новые, recommend = рекомендуемые
) -> list[SecondStreetItem]:
    """
    Парсер 2ndstreet.jp — японский ресейл магазин
    sort_by: arrival (новые), recommend, cost-low, cost-high, discount-high
    """
    
    url = "https://www.2ndstreet.jp/search"
    params = {
        "keyword": keyword,
        "sortBy": sort_by,
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.5",
    }
    
    try:
        async with session.get(
            url,
            params=params,
            headers=headers,
            proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=30),
            ssl=False,
        ) as resp:
            if resp.status != 200:
                print(f"[2ndStreet] HTTP {resp.status}")
                return []
            
            html = await resp.text()
    except Exception as e:
        print(f"[2ndStreet] Request error: {e}")
        return []
    
    items = []
    
    # Метод 1: парсим dataLayer JSON (самый надежный)
    # Ищем impressions или view_item_list с данными товаров
    dl_pattern = re.compile(
        r"dataLayer\.push\(\{[^}]*'event':'impressionsGNW'[^}]*'ecommerce':\{[^}]*'impressions':\[([^\]]+)\]",
        re.DOTALL
    )
    
    # Проще — ищем все impressions блоки
    impressions_pattern = re.compile(
        r"'impressions':\[(.+?)\]\}", re.DOTALL
    )
    
    all_impressions = []
    for match in impressions_pattern.finditer(html):
        try:
            # Конвертируем JS объект в JSON
            js_obj = match.group(1)
            # Заменяем одиночные кавычки на двойные
            js_obj = re.sub(r"'([^']+)':", r'"\1":', js_obj)
            js_obj = re.sub(r":\s*'([^']*)'", r': "\1"', js_obj)
            js_obj = js_obj.rstrip(",")
            parsed = json.loads(f"[{js_obj}]")
            all_impressions.extend(parsed)
        except Exception:
            pass
    
    # Метод 2: парсим HTML карточки
    card_pattern = re.compile(
        r'<li[^>]+goodsid="(\d+)"[^>]+>.*?'
        r'<a href="(/goods/detail/goodsId/\d+/shopsId/\d+)".*?>'
        r'.*?<img src="([^"]+)".*?>'
        r'.*?<p class="itemCard_brand">([^<]+)</p>'
        r'.*?<p class="itemCard_name">([^<]+)</p>'
        r'(?:.*?<p class="itemCard_size">([^<]*)</p>)?'
        r'(?:.*?<p class="itemCard_status">([^<]*)</p>)?'
        r'.*?<p class="itemCard_price[^"]*">¥([\d,]+)',
        re.DOTALL
    )
    
    seen_ids = set()
    
    for m in card_pattern.finditer(html):
        goods_id = m.group(1)
        if goods_id in seen_ids:
            continue
        seen_ids.add(goods_id)
        
        item_url = f"https://www.2ndstreet.jp{m.group(2)}"
        image_url = m.group(3)
        brand = m.group(4).strip()
        name = m.group(5).strip()
        size = m.group(6).strip() if m.group(6) else None
        if size:
            size = size.replace("サイズ", "").strip()
        condition = m.group(7).strip() if m.group(7) else None
        if condition:
            condition = condition.replace("商品の状態 : ", "").strip()
        price_str = m.group(8).replace(",", "")
        price = int(price_str)
        
        # Формируем название из бренда + имени
        full_title = f"{brand} {name}" if brand not in name else name
        
        item = SecondStreetItem(
            title=full_title,
            price=price,
            currency="¥",
            url=item_url,
            image_url=image_url,
            brand=brand,
            size=size,
            condition=condition,
            goods_id=goods_id,
        )
        items.append(item)
        
        if len(items) >= max_items:
            break
    
    return items


async def _test():
    proxy = "socks5://31.130.132.149:1080"
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        items = await search_secondstreet(
            keyword="nike",
            session=session,
            proxy=proxy,
            sort_by="arrival",
        )
        print(f"Найдено: {len(items)}")
        for item in items[:5]:
            print(f"  [{item.brand}] {item.title[:50]}")
            print(f"    Цена: {item.currency}{item.price}")
            print(f"    Размер: {item.size}, Состояние: {item.condition}")
            print(f"    URL: {item.url}")


if __name__ == "__main__":
    asyncio.run(_test())
