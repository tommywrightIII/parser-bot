import asyncio
import json
import re
from typing import Optional
from datetime import datetime
from dataclasses import dataclass
from playwright.async_api import async_playwright


@dataclass
class XianyuItem:
    id: str
    name: str
    price: float
    condition: str
    size: Optional[str]
    image_url: str
    url: str
    seller: str
    status: str
    created_at: Optional[datetime] = None


CONDITION_MAP = {
    "new": "10/10 Новый",
    "like_new": "9/10 Почти новый",
    "good": "8/10 Хорошее",
    "fair": "6/10 Среднее",
    "poor": "4/10 Плохое",
}


async def search_xianyu(query, min_price=0, max_price=999999, condition=None, size=None, limit=10, proxy=None, category_id=None):
    results = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                proxy={"server": "http://127.0.0.1:8900"},
            )
            context = await browser.new_context(
                locale="zh-CN",
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            )

            api_future = asyncio.get_event_loop().create_future()

            async def handle_response(response):
                if any(x in response.url for x in ["search", "mtop", "items"]) and response.status == 200:
                    try:
                        data = await response.json()
                        items = _extract_items(data)
                        if items and not api_future.done():
                            api_future.set_result(items)
                    except:
                        pass

            page = await context.new_page()
            page.on("response", handle_response)

            url = f"https://2.taobao.com/search?q={query}"
            print(f"[Xianyu] Открываем: {url}")
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")

            try:
                items = await asyncio.wait_for(api_future, timeout=20)
                print(f"[Xianyu] Получено: {len(items)}")

                for item in items[:limit]:
                    price = float(item.get("price", 0) or item.get("soldPrice", 0) or 0)
                    if min_price > 0 and price < min_price:
                        continue
                    if max_price < 999999 and price > max_price:
                        continue

                    img = item.get("picUrl", "") or item.get("pic", "")
                    if img and img.startswith("//"):
                        img = "https:" + img

                    item_id = str(item.get("itemId", "") or item.get("id", ""))

                    results.append(XianyuItem(
                        id=item_id,
                        name=item.get("title", "") or item.get("name", ""),
                        price=price,
                        condition="Не указано",
                        size=None,
                        image_url=img,
                        url=f"https://2.taobao.com/item.htm?id={item_id}",
                        seller=item.get("userNick", "") or item.get("seller", ""),
                        status="В продаже",
                    ))

            except asyncio.TimeoutError:
                print("[Xianyu] Таймаут")

            await browser.close()

    except Exception as e:
        print(f"[Xianyu] Ошибка: {e}")

    print(f"[Xianyu] Найдено: {len(results)}")
    return results


def _extract_items(data):
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        if any(k in data[0] for k in ["price", "soldPrice", "title", "itemId"]):
            return data
    if isinstance(data, dict):
        for key in ["items", "list", "data", "result", "resultList", "auctions"]:
            if key in data:
                result = _extract_items(data[key])
                if result:
                    return result
    return []
