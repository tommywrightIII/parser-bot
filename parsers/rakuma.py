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

    # Прокси: сначала из параметра, потом из env
    proxy_url = proxy or os.environ.get("PROXY_URL")
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
            logging.info(f"[Rakuma] Открываем: {url}")

            # commit вместо networkidle — не ждём полной загрузки
            await page.goto(url, timeout=60000, wait_until="commit")
            
            # Ждём появления карточек товаров
            try:
                await page.wait_for_selector("li.item", timeout=15000)
            except Exception:
                logging.warning("[Rakuma] Селектор li.item не найден, пробуем другой")
                try:
                    await page.wait_for_selector("[class*='item']", timeout=10000)
                except Exception:
                    logging.warning("[Rakuma] Карточки не найдены")

            await asyncio.sleep(2)

            title = await page.title()
            logging.info(f"[Rakuma] Заголовок: {title}")

            # Дебаг: смотрим HTML структуру
            body_html = await page.evaluate("() => document.body.innerHTML.slice(0, 3000)")
            logging.info(f"[Rakuma] HTML: {body_html}")

            # Пробуем найти товары
            items = await page.query_selector_all("li.item")
            logging.info(f"[Rakuma] li.item найдено: {len(items)}")

            if not items:
                items = await page.query_selector_all("[class*='item-box']")
                logging.info(f"[Rakuma] item-box найдено: {len(items)}")

            if not items:
                items = await page.query_selector_all("article")
                logging.info(f"[Rakuma] article найдено: {len(items)}")

            for item in items[:limit]:
                try:
                    # Ссылка и ID
                    link_el = await item.query_selector("a")
                    item_url = await link_el.get_attribute("href") if link_el else ""
                    if item_url and not item_url.startswith("http"):
                        item_url = "https://fril.jp" + item_url
                    item_id = item_url.split("/")[-1] if item_url else ""

                    # Название
                    name_el = await item.query_selector("[class*='name'], [class*='title'], img")
                    name = ""
                    if name_el:
                        name = await name_el.get_attribute("alt") or await name_el.inner_text()
                    name = name.strip()

                    # Цена
                    price_el = await item.query_selector("[class*='price']")
                    price_text = await price_el.inner_text() if price_el else "0"
                    price = int(re.sub(r"[^\d]", "", price_text) or 0)

                    # Картинка
                    img_el = await item.query_selector("img")
                    image_url = await img_el.get_attribute("src") if img_el else ""

                    if not name or price == 0:
                        continue

                    if price < min_price or price > max_price:
                        continue

                    results.append(RakumaItem(
                        id=item_id,
                        name=name,
                        price=price,
                        condition="",
                        size=_extract_size(name),
                        image_url=image_url,
                        url=item_url,
                        seller="",
                        status="on_sale",
                    ))
                except Exception as e:
                    logging.warning(f"[Rakuma] Ошибка парсинга элемента: {e}")
                    continue

            await browser.close()

    except Exception as e:
        logging.error(f"[Rakuma] Ошибка: {e}")
        import traceback
        logging.error(traceback.format_exc())

    logging.info(f"[Rakuma] Найдено: {len(results)}")
    return results
