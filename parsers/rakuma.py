import asyncio
import logging
import os
import re
import json
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

    # URL с правильным encoding японского текста
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
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                extra_http_headers={"Accept-Language": "ja-JP,ja;q=0.9"},
            )
            page = await context.new_page()

            # Перехватываем XHR/fetch запросы чтобы найти API
            api_response_data = []

            async def handle_response(response):
                if "fril.jp" in response.url and response.status == 200:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        try:
                            data = await response.json()
                            logging.info(f"[Rakuma] XHR JSON: {response.url} → ключи: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                            api_response_data.append((response.url, data))
                        except Exception:
                            pass

            page.on("response", handle_response)

            await page.goto(search_url, timeout=60000, wait_until="domcontentloaded")

            # Ждём появления товаров — пробуем разные селекторы
            selectors_to_try = [
                "[class*='item']",
                "[class*='product']",
                "[class*='card']",
                "li a[href*='/item/']",
                "a[href*='/item/']",
            ]

            found_selector = None
            for selector in selectors_to_try:
                try:
                    await page.wait_for_selector(selector, timeout=8000)
                    count = len(await page.query_selector_all(selector))
                    logging.info(f"[Rakuma] Селектор '{selector}': {count} элементов")
                    if count > 3:
                        found_selector = selector
                        break
                except Exception:
                    logging.info(f"[Rakuma] Селектор '{selector}': не найден")

            await asyncio.sleep(3)

            # Логируем заголовок страницы
            title = await page.title()
            logging.info(f"[Rakuma] Заголовок: {title}")

            # Если нашли XHR данные — используем их
            if api_response_data:
                logging.info(f"[Rakuma] Найдено XHR ответов: {len(api_response_data)}")
                for url_found, data in api_response_data:
                    logging.info(f"[Rakuma] XHR URL: {url_found}")

            # Пробуем найти товары через ссылки на /item/
            item_links = await page.query_selector_all("a[href*='/item/']")
            logging.info(f"[Rakuma] Ссылок на товары: {len(item_links)}")

            seen_ids = set()
            for link in item_links[:limit * 2]:
                try:
                    href = await link.get_attribute("href")
                    if not href:
                        continue
                    if not href.startswith("http"):
                        href = "https://fril.jp" + href

                    # Извлекаем ID из URL
                    id_match = re.search(r'/item/([a-zA-Z0-9]+)', href)
                    if not id_match:
                        continue
                    item_id = id_match.group(1)
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)

                    # Ищем картинку внутри ссылки
                    img = await link.query_selector("img")
                    image_url = await img.get_attribute("src") if img else ""
                    name = await img.get_attribute("alt") if img else ""

                    # Ищем цену рядом
                    parent = await link.evaluate_handle("el => el.closest('li') || el.parentElement")
                    price_el = await parent.query_selector("[class*='price'], [class*='Price']")
                    price_text = await price_el.inner_text() if price_el else "0"
                    price = int(re.sub(r"[^\d]", "", price_text) or 0)

                    if not name and not image_url:
                        continue
                    if price == 0:
                        continue
                    if price < min_price or price > max_price:
                        continue

                    results.append(RakumaItem(
                        id=item_id,
                        name=name or item_id,
                        price=price,
                        condition="",
                        size=_extract_size(name or ""),
                        image_url=image_url,
                        url=href,
                        seller="",
                        status="on_sale",
                    ))

                    if len(results) >= limit:
                        break

                except Exception as e:
                    logging.warning(f"[Rakuma] Ошибка парсинга ссылки: {e}")
                    continue

            # Если всё ещё ничего — логируем HTML для отладки
            if not results:
                html = await page.content()
                logging.info(f"[Rakuma] HTML длина: {len(html)}")
                # Ищем ссылки на item в HTML
                item_hrefs = re.findall(r'href="(/item/[^"]+)"', html)
                logging.info(f"[Rakuma] href /item/ в HTML: {len(item_hrefs)} → {item_hrefs[:5]}")

            await browser.close()

    except Exception as e:
        logging.error(f"[Rakuma] Ошибка: {e}")
        import traceback
        logging.error(traceback.format_exc())

    logging.info(f"[Rakuma] Найдено: {len(results)}")
    return results
