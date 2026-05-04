import asyncio
import json
import re
import os
from typing import Optional
from datetime import datetime
from dataclasses import dataclass
from playwright.async_api import async_playwright


@dataclass
class App95Item:
    id: str
    name: str
    price: float
    condition: str
    size: Optional[str]
    image_url: str
    url: str
    seller: str
    status: str
    brand: str = ""
    created_at: Optional[datetime] = None


CONDITION_MAP = {
    "10": "10/10 Новый",
    "9": "9/10 Почти новый",
    "8": "8/10 Хорошее",
    "7": "7/10 Среднее",
    "6": "6/10 Ниже среднего",
    "new": "10/10 Новый",
    "like_new": "9/10 Почти новый",
    "good": "8/10 Хорошее",
    "fair": "7/10 Среднее",
    "poor": "6/10 Плохое",
}


async def search_95app(query, min_price=0, max_price=999999, condition=None, size=None, limit=10, proxy=None, category_id=None):
    results = []
    
    # Используем гонконгский прокси
    cn_proxy = os.getenv("PROXY_CN", proxy)
    proxy_config = None
    if cn_proxy:
        match = re.match(r'(socks5|http)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)', cn_proxy)
        if match:
            proto, user, password, host, port = match.groups()
            proxy_config = {"server": "http://127.0.0.1:8900"}
            if user:
                proxy_config["username"] = user
            if password:
                proxy_config["password"] = password

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                proxy=proxy_config,
            )
            context = await browser.new_context(
                locale="zh-CN",
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            )

            api_future = asyncio.get_event_loop().create_future()

            async def handle_response(response):
                if any(x in response.url for x in ["search", "product", "items", "goods"]) and response.status == 200:
                    try:
                        data = await response.json()
                        items = _extract_items(data)
                        if items and not api_future.done():
                            api_future.set_result(items)
                    except:
                        pass

            page = await context.new_page()
            page.on("response", handle_response)

            url = f"https://m.95.cn/search?keyword={query}"
            print(f"[95App] Открываем: {url}")
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")

            try:
                items = await asyncio.wait_for(api_future, timeout=20)
                print(f"[95App] Получено: {len(items)}")

                for item in items[:limit]:
                    price = float(item.get("price", 0) or item.get("sellPrice", 0) or 0)
                    if min_price > 0 and price < min_price:
                        continue
                    if max_price < 999999 and price > max_price:
                        continue

                    grade = str(item.get("grade", ""))
                    item_condition = CONDITION_MAP.get(grade, "Не указано")
                    if condition and condition in CONDITION_MAP:
                        pass

                    img = item.get("mainPic", "") or item.get("pic", "") or item.get("image", "")
                    if img and img.startswith("//"):
                        img = "https:" + img

                    item_id = str(item.get("id", "") or item.get("productId", ""))

                    results.append(App95Item(
                        id=item_id,
                        name=item.get("name", "") or item.get("title", ""),
                        price=price,
                        condition=item_condition,
                        size=item.get("sizeStr", None) or item.get("size", None),
                        image_url=img,
                        url=f"https://www.95.cn/detail/{item_id}",
                        seller=item.get("shopName", "") or item.get("sellerName", ""),
                        status="В продаже",
                        brand=item.get("brandName", ""),
                    ))

            except asyncio.TimeoutError:
                print("[95App] Таймаут ожидания API")

            await browser.close()

    except Exception as e:
        print(f"[95App] Ошибка: {e}")

    print(f"[95App] Найдено: {len(results)}")
    return results


def _extract_items(data):
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        if any(k in data[0] for k in ["price", "sellPrice", "name", "title"]):
            return data
    if isinstance(data, dict):
        for key in ["list", "items", "data", "products", "result", "records"]:
            if key in data:
                result = _extract_items(data[key])
                if result:
                    return result
    return []
