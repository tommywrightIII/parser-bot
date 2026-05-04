import asyncio
import re
from typing import Optional
from datetime import datetime
from dataclasses import dataclass
from playwright.async_api import async_playwright


@dataclass
class YahooItem:
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


async def search_yahoo(query, min_price=0, max_price=999999, condition=None, size=None, limit=10, proxy=None, category_id=None):
    results = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, proxy={"server": "http://127.0.0.1:8899"})
            page = await browser.new_page()
            url = f"https://auctions.yahoo.co.jp/search/search?p={query}&order=1&f=0x2"
            if min_price > 0:
                url += f"&min={min_price}"
            if max_price < 999999:
                url += f"&max={max_price}"
            print(f"[Yahoo] {url}")
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(1)

            items_data = await page.evaluate(f"""
                () => {{
                    const cards = Array.from(document.querySelectorAll('li.Product')).slice(0, {limit * 2});
                    return cards.map(card => {{
                        const a = card.querySelector('a');
                        const img = card.querySelector('img');
                        const title = card.querySelector('.Product__title');
                        const price = card.querySelector('.Product__priceValue');
                        const condition = card.querySelector('.Product__condition, .Condition');
                        const seller = card.querySelector('.Product__seller, .Seller__name');
                        return {{
                            href: a ? a.href : '',
                            img: img ? (img.src || img.getAttribute('data-src') || '') : '',
                            name: title ? title.textContent.trim() : '',
                            price: price ? price.textContent.trim() : '0',
                            condition: condition ? condition.textContent.trim() : '',
                            seller: seller ? seller.textContent.trim() : '',
                        }};
                    }});
                }}
            """)

            print(f"[Yahoo] Карточек: {len(items_data)}")

            for d in items_data:
                href = d.get('href', '')
                name = d.get('name', '')
                img_url = d.get('img', '')
                if not href or not name:
                    continue
                m = re.search(r'[/=]([a-z]\d+)', href)
                item_id = m.group(1) if m else href.split('/')[-1]
                price = int(re.sub(r'[^\d]', '', d.get('price', '0')) or '0')
                if min_price > 0 and price < min_price:
                    continue
                if max_price < 999999 and price > max_price:
                    continue
                print(f"[Yahoo] {name[:25]} | {price} | img={'yes' if img_url else 'NO'} | {img_url[:60]}")
                results.append(YahooItem(
                    id=item_id, name=name, price=price,
                    condition=d.get('condition', '') or 'Не указано', size=_extract_size(name),
                    image_url=img_url, url=href, seller="", status="В продаже"
                ))
                if len(results) >= limit:
                    break

            await browser.close()
    except Exception as e:
        print(f"[Yahoo] Ошибка: {e}")
    print(f"[Yahoo] Итого: {len(results)}")
    return results
