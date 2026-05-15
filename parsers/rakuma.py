import asyncio
import logging
import os
import re
import json
import base64
from typing import Optional
from datetime import datetime
from dataclasses import dataclass
from urllib.parse import urlencode, quote
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

    proxy_url = proxy or os.environ.get("PROXY_URL")
    proxy_config = None
    if proxy_url:
        match = re.match(r'(socks5|https?)://(?:([^:@]+):([^@]+)@)?([^:]+):(\d+)', proxy_url)
        if match:
            proto, user, password, host, port = match.groups()
            proxy_config = {"server": f"{proto}://{host}:{port}"}
            if user and password:
                proxy_config["username"] = user
                proxy_config["password"] = password
        else:
            proxy_config = {"server": proxy_url}

    params = {
        "query": translated_query,
        "sort": "created_at",
        "order": "desc",
        "status": "on_sale",
    }
    if min_price > 0:
        params["price_min"] = min_price
    if max_price < 999999:
        params["price_max"] = max_price

    search_url = "https://fril.jp/s?" + urlencode(params, quote_via=quote)
    logging.info(f"[Rakuma] URL: {search_url}")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                proxy=proxy_config,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                locale="ja-JP",
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                extra_http_headers={"Accept-Language": "ja-JP,ja;q=0.9"},
            )
            page = await context.new_page()

            await page.goto(search_url, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(5)

            # Логируем реальный URL после редиректов
            current_url = page.url
            title = await page.title()
            logging.info(f"[Rakuma] Реальный URL: {current_url}")
            logging.info(f"[Rakuma] Заголовок: {title}")

            # Скриншот для отладки — сохраняем в base64 в лог
            screenshot = await page.screenshot(full_page=False)
            screenshot_b64 = base64.b64encode(screenshot).decode()
            logging.info(f"[Rakuma] SCREENSHOT_BASE64:{screenshot_b64[:100]}...")

            # Сохраняем скриншот на диск
            with open("/tmp/rakuma_debug.png", "wb") as f:
                f.write(screenshot)
            logging.info(f"[Rakuma] Скриншот сохранён в /tmp/rakuma_debug.png")

            # Логируем весь текст страницы
            body_text = await page.evaluate("() => document.body.innerText.slice(0, 1000)")
            logging.info(f"[Rakuma] Текст страницы: {body_text}")

            # Все ссылки на странице
            all_links = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(h => h.includes('fril.jp'))
                    .slice(0, 30)
            }""")
            logging.info(f"[Rakuma] Все ссылки fril.jp: {all_links}")

            await browser.close()

    except Exception as e:
        logging.error(f"[Rakuma] Ошибка: {e}")
        import traceback
        logging.error(traceback.format_exc())

    logging.info(f"[Rakuma] Найдено: {len(results)}")
    return results
