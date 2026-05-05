import asyncio
import logging
import re
import os
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
            proxy_url = os.environ.get("PROXY_URL")
            proxy_config = None
            if proxy_url:
                match = re.match(r'(https?|socks5)://([^:@]+):([^@]+)@([^:]+):(\d+)', proxy_url)
                if match:
                    proto, user, password, host, port = match.groups()
                    proxy_config = {
                        "server": f"{proto}://{host}:{port}",
                        "username": user,
                        "password": password,
                    }
                else:
                    proxy_config = {"server": proxy_url}

            logging.info(f"[Yahoo] Запуск поиска: {query}")
            browser = await p.chromium.launch(
                headless=True,
                proxy=proxy_config,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                locale="ja-JP",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                extra_http_headers={
                    "Accept-Language": "ja-JP,ja;q=0.9",
                }
            )
            page = await context.new_page()
            url = f"https://auctions.yahoo.co.jp/search/search?p={query}&order=1&f=0x2"
            if min_price > 0:
                url += f"&min={min_price}"
            if max_price < 999999:
                url += f"&max={max_price}"

            logging.info(f"[Yahoo] Открываем: {url}")
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            items_data = await page.evaluate(f"""
                () => {{
                    const selectors = [
                        'li.Product',
                        '.SearchResult li',
                        '[data-auction-id]',
                        '.Product',
                    ];
                    let cards = [];
                    for (const sel of selectors) {{
                        cards = Array.from(document.querySelectorAll(sel)).slice(0, {limit * 2});
                        if (cards.length > 0) break;
                    }}
                    console.log('Found cards:', cards.length);
                    return cards.map(card => {{
                        const a = card.querySelector('a[href*="auctions.yahoo"]') || card.querySelector('a');
                        const img = card.querySelector('img');
                        const title = card.querySelector('.Product__title, .title, h3, .itemName') ;
                        const price = card.querySelector('.Product__priceValue, .price, .Price, [class*="price"]');
                        return {{
                            href: a ? a.href : '',
                            img: img ? (img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy') || '') : '',
                            name: title ? title.textContent.trim() : (card.querySelector('a') ? card.querySelector('a').textContent.trim() : ''),
                            price: price ? price.textContent.trim() : '0',
                            condition: '',
                            seller: '',
                        }};
                    }});
                }}
            """)

            logging.info(f"[Yahoo] Карточек: {len(items_data)}")

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
                results.append(YahooItem(
                    id=item_id, name=name, price=price,
                    condition=d.get('condition', '') or 'Не указано',
                    size=_extract_size(name),
                    image_url=img_url, url=href, seller="", status="В продаже"
                ))
                if len(results) >= limit:
                    break

            await browser.close()
    except Exception as e:
        logging.error(f"[Yahoo] Ошибка: {e}")
        import traceback
        logging.error(traceback.format_exc())

    logging.info(f"[Yahoo] Итого: {len(results)}")
    return results
