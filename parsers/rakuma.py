import asyncio
import logging
import os
import re
from typing import Optional
from datetime import datetime
from dataclasses import dataclass
from playwright.async_api import async_playwright
from deep_translator import GoogleTranslator


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


def _is_japanese(text: str) -> bool:
    for ch in text:
        if '\u3040' <= ch <= '\u30ff' or '\u4e00' <= ch <= '\u9fff':
            return True
    return False


async def _translate_to_japanese(query: str) -> str:
    if _is_japanese(query):
        return query
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: GoogleTranslator(source='auto', target='ja').translate(query)
        )
        logging.info(f"[Rakuma] Перевод: {query} → {result}")
        return result
    except Exception as e:
        logging.warning(f"[Rakuma] Ошибка перевода: {e}")
        return query


async def search_rakuma(query, min_price=0, max_price=999999, condition=None, size=None, limit=10, proxy=None, category_id=None):
    results = []

    translated_query = await _translate_to_japanese(query)

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

    try:
        async with async_playwright() as p:
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
            url = f"https://fril.jp/s?query={translated_query}&sort=created_at&order=desc&status=selling"
            if min_price > 0:
                url += f"&min_price={min_price}"
            if max_price < 999999:
                url += f"&max_price={max_price}"

            logging.info(f"[Rakuma] Открываем: {url}")
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            debug_info = await page.evaluate("""
                () => {
                    const cards = Array.from(document.querySelectorAll('.items__listItem, [class*="item"], li, article')).slice(0, 3);
                    return cards.map(card => ({
                        tag: card.tagName,
                        class: card.className,
                        html: card.innerHTML.slice(0, 300),
                        href: card.querySelector('a') ? card.querySelector('a').href : '',
                        imgAlt: card.querySelector('img') ? card.querySelector('img').alt : '',
                    }));
                }
            """)

            for i, d in enumerate(debug_info):
                logging.info(f"[Rakuma] Card {i}: tag={d['tag']} class={d['class'][:50]} href={d['href'][:80]} alt={d['imgAlt'][:50]}")

            await browser.close()

    except Exception as e:
        logging.error(f"[Rakuma] Ошибка: {e}")
        import traceback
        logging.error(traceback.format_exc())

    logging.info(f"[Rakuma] Найдено: {len(results)}")
    return results
