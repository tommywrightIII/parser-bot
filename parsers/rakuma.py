import asyncio
import re
from typing import Optional
from datetime import datetime
from dataclasses import dataclass
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup


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


async def search_rakuma(query, min_price=0, max_price=999999, condition=None, size=None, limit=10, proxy=None, category_id=None):
    results = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                proxy={"server": "http://127.0.0.1:8899"},
            )
            context = await browser.new_context(
                locale="ja-JP",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )

            page = await context.new_page()
            url = f"https://fril.jp/s?query={query}&sort=created_at&order=desc&status=selling"
            if min_price > 0:
                url += f"&min_price={min_price}"
            if max_price < 999999:
                url += f"&max_price={max_price}"

            print(f"[Rakuma] Открываем: {url}")
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(1)

            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            cards = soup.select(".items__listItem")
            print(f"[Rakuma] Карточек: {len(cards)}")

            for card in cards[:limit]:
                try:
                    # Ссылка и ID
                    link = card.select_one("a")
                    if not link:
                        continue
                    href = link.get("href", "")
                    match = re.search(r"/items/(\w+)", href)
                    if not match:
                        continue
                    item_id = match.group(1)
                    item_url = f"https://fril.jp{href}" if href.startswith("/") else href

                    # Название из alt картинки
                    img = card.select_one("img")
                    name = ""
                    if img:
                        name = img.get("alt", "")
                    if not name:
                        name = card.get_text(strip=True)[:50]

                    # Цена
                    price_el = card.select_one("[class*='price'], [class*='Price']")
                    price = 0
                    if price_el:
                        price_text = price_el.get_text(strip=True)
                        price = int(re.sub(r"[^\d]", "", price_text) or "0")

                    # Фото
                    img_url = ""
                    if img:
                        img_url = img.get("src", "") or img.get("data-src", "")
                        if img_url.startswith("//"):
                            img_url = "https:" + img_url

                    if min_price > 0 and price < min_price:
                        continue
                    if max_price < 999999 and price > max_price:
                        continue

                    results.append(RakumaItem(
                        id=item_id,
                        name=name,
                        price=price,
                        condition="Не указано",
                        size=_extract_size(name),
                        image_url=img_url,
                        url=item_url,
                        seller="",
                        status="В продаже",
                    ))
                except Exception as e:
                    print(f"[Rakuma] Ошибка карточки: {e}")
                    continue

            await browser.close()

    except Exception as e:
        print(f"[Rakuma] Ошибка: {e}")

    print(f"[Rakuma] Найдено: {len(results)}")
    return results
