import asyncio
import aiohttp
import xml.etree.ElementTree as ET
import logging
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command

from parsers.mercari import search_mercari, format_date
from parsers.bunjang import search_bunjang, BunjangItem, BUNJANG_CATEGORIES
from parsers.grailed import search_grailed, GrailedItem, GRAILED_CATEGORIES
from parsers.categories import CATEGORIES, CATEGORY_GROUPS
from config import PROXY_URL

router = Router()
_shown_items: dict = {}
_cancelled: set = set()
_last_search: dict = {}
_cached_rate = {"rate": 0.62, "date": None}
_cached_usd_rate = {"rate": 90.0, "date": None}

PLATFORM_NAMES = {
    "mercari": "Mercari Japan 🇯🇵",
    "bunjang": "Bunjang 🇰🇷",
    "grailed": "Grailed 🇺🇸",
}


async def _get_yen_rate() -> float:
    import datetime
    today = datetime.date.today().isoformat()
    if _cached_rate["date"] == today:
        return _cached_rate["rate"]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.cbr.ru/scripts/XML_daily.asp", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                text = await resp.text(encoding="windows-1251")
                root = ET.fromstring(text)
                for valute in root.findall("Valute"):
                    char_code = valute.find("CharCode")
                    if char_code is not None and char_code.text == "JPY":
                        value = valute.find("Value").text.replace(",", ".")
                        nominal = int(valute.find("Nominal").text)
                        rate = float(value) / nominal
                        _cached_rate["rate"] = rate
                        _cached_rate["date"] = today
                        return rate
    except Exception as e:
        logging.warning(f"Ошибка получения курса ЦБ: {e}")
    return _cached_rate["rate"]


async def _get_krw_rate() -> float:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.cbr.ru/scripts/XML_daily.asp", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                text = await resp.text(encoding="windows-1251")
                root = ET.fromstring(text)
                for valute in root.findall("Valute"):
                    char_code = valute.find("CharCode")
                    if char_code is not None and char_code.text == "KRW":
                        value = valute.find("Value").text.replace(",", ".")
                        nominal = int(valute.find("Nominal").text)
                        return float(value) / nominal
    except Exception as e:
        logging.warning(f"Ошибка получения курса KRW: {e}")
    return 0.067


async def _get_usd_rate() -> float:
    import datetime
    today = datetime.date.today().isoformat()
    if _cached_usd_rate["date"] == today:
        return _cached_usd_rate["rate"]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.cbr.ru/scripts/XML_daily.asp", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                text = await resp.text(encoding="windows-1251")
                root = ET.fromstring(text)
                for valute in root.findall("Valute"):
                    char_code = valute.find("CharCode")
                    if char_code is not None and char_code.text == "USD":
                        value = valute.find("Value").text.replace(",", ".")
                        nominal = int(valute.find("Nominal").text)
                        rate = float(value) / nominal
                        _cached_usd_rate["rate"] = rate
                        _cached_usd_rate["date"] = today
                        return rate
    except Exception as e:
        logging.warning(f"Ошибка получения курса USD: {e}")
    return _cached_usd_rate["rate"]


class SearchForm(StatesGroup):
    choosing_search_type = State()
    choosing_platform = State()
    entering_query = State()
    choosing_category = State()
    choosing_bunjang_category = State()
    choosing_grailed_category = State()
    entering_size = State()
    entering_condition = State()
    entering_price = State()
    entering_date = State()


def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Поиск", callback_data="goto_search")],
    ])


def search_type_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗂 По категории", callback_data="stype_category")],
        [InlineKeyboardButton(text="🔎 По запросу", callback_data="stype_query")],
    ])


def platform_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Mercari 🇯🇵", callback_data="platform_mercari")],
        [InlineKeyboardButton(text="Bunjang 🇰🇷", callback_data="platform_bunjang")],
        [InlineKeyboardButton(text="Grailed 🇺🇸", callback_data="platform_grailed")],
    ])


def category_group_keyboard():
    buttons = []
    for group_name in CATEGORY_GROUPS:
        buttons.append([InlineKeyboardButton(text=group_name, callback_data=f"catgroup_{group_name}")])
    buttons.append([InlineKeyboardButton(text="⏭ Все категории", callback_data="cat_skip")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def category_keyboard(group_name: str):
    keys = CATEGORY_GROUPS[group_name]
    buttons = []
    row = []
    for key in keys:
        row.append(InlineKeyboardButton(text=CATEGORIES[key]["name"], callback_data=f"cat_{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="cat_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def grailed_category_keyboard():
    buttons = []
    for key, val in GRAILED_CATEGORIES.items():
        buttons.append([InlineKeyboardButton(text=val["name"], callback_data=f"gcat_{key}")])
    buttons.append([InlineKeyboardButton(text="⏭ Все категории", callback_data="gcat_skip")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def bunjang_category_keyboard():
    buttons = []
    for key, val in BUNJANG_CATEGORIES.items():
        buttons.append([InlineKeyboardButton(text=val["name"], callback_data=f"bcat_{key}")])
    buttons.append([InlineKeyboardButton(text="⏭ Все категории", callback_data="bcat_skip")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def condition_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="10/10 Новый", callback_data="cond_new"),
            InlineKeyboardButton(text="9/10 Почти новый", callback_data="cond_like_new"),
        ],
        [
            InlineKeyboardButton(text="8/10 Хорошее", callback_data="cond_good"),
            InlineKeyboardButton(text="6/10 Среднее", callback_data="cond_fair"),
        ],
        [
            InlineKeyboardButton(text="4/10 Плохое", callback_data="cond_poor"),
            InlineKeyboardButton(text="🔄 Любое", callback_data="cond_any"),
        ],
    ])


def date_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚡ Свежие (за час)", callback_data="date_1h"),
            InlineKeyboardButton(text="📅 За день", callback_data="date_24h"),
        ],
        [
            InlineKeyboardButton(text="📆 За 3 дня", callback_data="date_72h"),
            InlineKeyboardButton(text="🗓 За неделю", callback_data="date_7d"),
        ],
        [InlineKeyboardButton(text="🔄 Любое время", callback_data="date_any")],
    ])


def skip_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip")]
    ])


def results_count_keyboard(show_all=False):
    buttons = [[
        InlineKeyboardButton(text="5", callback_data="count_5"),
        InlineKeyboardButton(text="10", callback_data="count_10"),
        InlineKeyboardButton(text="20", callback_data="count_20"),
        InlineKeyboardButton(text="50", callback_data="count_50"),
        InlineKeyboardButton(text="100", callback_data="count_100"),
    ]]
    if show_all:
        buttons.append([InlineKeyboardButton(text="🔥 Все за час", callback_data="count_all")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👟 <b>Resale Parser Bot</b>\n\nВыбери действие:",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("Главное меню:", reply_markup=main_keyboard())


@router.message(Command("clear"))
async def cmd_clear(message: Message):
    user_id = str(message.from_user.id)
    _shown_items.pop(user_id, None)
    await message.answer("✅ История сброшена.")


@router.message(Command("stop"))
async def cmd_stop(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    _cancelled.add(user_id)
    await state.clear()
    await message.answer("⛔ Поиск остановлен.", reply_markup=main_keyboard())


@router.callback_query(F.data == "goto_search")
async def goto_search(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🔍 <b>Поиск</b>\n\nКак искать?",
        reply_markup=search_type_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SearchForm.choosing_search_type)
    await callback.answer()


@router.callback_query(SearchForm.choosing_search_type, F.data == "stype_category")
async def stype_category(callback: CallbackQuery, state: FSMContext):
    await state.update_data(mode="category", query="")
    await callback.message.edit_text(
        "🗂 <b>Поиск по категории</b>\n\nВыбери платформу:",
        reply_markup=platform_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SearchForm.choosing_platform)
    await callback.answer()


@router.callback_query(SearchForm.choosing_search_type, F.data == "stype_query")
async def stype_query(callback: CallbackQuery, state: FSMContext):
    await state.update_data(mode="search", category_id=None)
    await callback.message.edit_text(
        "🔎 <b>Поиск по запросу</b>\n\nВыбери платформу:",
        reply_markup=platform_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SearchForm.choosing_platform)
    await callback.answer()


@router.callback_query(SearchForm.choosing_platform, F.data.startswith("platform_"))
async def process_platform(callback: CallbackQuery, state: FSMContext):
    platform = callback.data.replace("platform_", "")
    data = await state.get_data()
    mode = data.get("mode", "search")
    await state.update_data(platform=platform)
    pname = PLATFORM_NAMES.get(platform, platform)

    if platform == "bunjang" and mode == "category":
        await callback.message.edit_text(
            f"✅ Платформа: <b>{pname}</b>\n\n📂 Выбери категорию:",
            reply_markup=bunjang_category_keyboard(),
            parse_mode="HTML"
        )
        await state.set_state(SearchForm.choosing_bunjang_category)
    elif platform == "grailed" and mode == "category":
        await callback.message.edit_text(
            f"✅ Платформа: <b>{pname}</b>\n\n📂 Выбери категорию:",
            reply_markup=grailed_category_keyboard(),
            parse_mode="HTML"
        )
        await state.set_state(SearchForm.choosing_grailed_category)
    elif mode == "category" and platform not in ["bunjang", "grailed",]:
        await callback.message.edit_text(
            f"✅ Платформа: <b>{pname}</b>\n\n📂 Выбери категорию:",
            reply_markup=category_group_keyboard(),
            parse_mode="HTML"
        )
        await state.set_state(SearchForm.choosing_category)
    else:
        await callback.message.edit_text(
            f"✅ Платформа: <b>{pname}</b>\n\nВведи поисковый запрос:",
            parse_mode="HTML"
        )
        await state.set_state(SearchForm.entering_query)
    await callback.answer()


@router.callback_query(SearchForm.choosing_bunjang_category, F.data.startswith("bcat_"))
async def process_bunjang_category(callback: CallbackQuery, state: FSMContext):
    key = callback.data.replace("bcat_", "")
    if key == "skip":
        await state.update_data(category_id=None, category_name="", query="")
        await callback.message.edit_text("📂 Категория: <b>все</b>", parse_mode="HTML")
    else:
        cat = BUNJANG_CATEGORIES.get(key, {})
        await state.update_data(
            category_id=cat.get("id"),
            category_name=cat.get("name", ""),
            query=cat.get("name", "")
        )
        await callback.message.edit_text(f"📂 Категория: <b>{cat.get('name', '')}</b>", parse_mode="HTML")

    await callback.message.answer(
        "🔎 Введи поисковый запрос (или пропусти):",
        reply_markup=skip_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SearchForm.entering_query)
    await callback.answer()


@router.callback_query(SearchForm.choosing_grailed_category, F.data.startswith("gcat_"))
async def process_grailed_category(callback: CallbackQuery, state: FSMContext):
    key = callback.data.replace("gcat_", "")
    if key == "skip":
        await state.update_data(category_id=None, category_name="", query="")
        await callback.message.edit_text("📂 Категория: <b>все</b>", parse_mode="HTML")
    else:
        cat = GRAILED_CATEGORIES.get(key, {})
        await state.update_data(
            category_id=cat.get("category"),
            category_name=cat.get("name", ""),
            query=cat.get("query", "")
        )
        await callback.message.edit_text(f"📂 Категория: <b>{cat.get('name', '')}</b>", parse_mode="HTML")

    await callback.message.answer(
        "🔎 Введи поисковый запрос (или пропусти):",
        reply_markup=skip_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(SearchForm.entering_query)
    await callback.answer()


@router.message(SearchForm.entering_query)
async def process_query(message: Message, state: FSMContext):
    await state.update_data(query=" ".join(message.text.split()))
    await message.answer("📐 Укажи размер (или пропусти):", reply_markup=skip_keyboard())
    await state.set_state(SearchForm.entering_size)


@router.callback_query(SearchForm.entering_query, F.data == "skip")
async def skip_query(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🔎 Запрос: <b>любой</b>", parse_mode="HTML")
    await callback.message.answer("📐 Укажи размер (или пропусти):", reply_markup=skip_keyboard())
    await state.set_state(SearchForm.entering_size)
    await callback.answer()


@router.callback_query(SearchForm.choosing_category, F.data.startswith("catgroup_"))
async def process_category_group(callback: CallbackQuery, state: FSMContext):
    group_name = callback.data.replace("catgroup_", "")
    await state.update_data(category_group=group_name)
    await callback.message.edit_text(
        f"📂 <b>{group_name}</b>\n\nВыбери подкатегорию:",
        reply_markup=category_keyboard(group_name),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(SearchForm.choosing_category, F.data == "cat_back")
async def category_back(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📂 Выбери категорию:", reply_markup=category_group_keyboard())
    await callback.answer()


@router.callback_query(SearchForm.choosing_category, F.data.startswith("cat_"))
async def process_category(callback: CallbackQuery, state: FSMContext):
    key = callback.data.replace("cat_", "")
    cat_jp = {
        "tshirts": "Tシャツ", "shirts": "シャツ", "hoodies": "パーカー",
        "jackets": "ジャケット", "pants": "パンツ", "shorts": "ショートパンツ",
        "suits": "スーツ", "sneakers": "スニーカー", "sandals": "サンダル",
        "boots": "ブーツ", "shoes": "革靴", "hats": "帽子", "bags": "バッグ",
        "belts": "ベルト", "glasses": "メガネ", "watches": "時計", "jewelry": "アクセサリー"
    }
    if key == "skip":
        await state.update_data(category_id=None, category_name="", query="")
        await callback.message.edit_text("📂 Категория: <b>все</b>", parse_mode="HTML")
    else:
        cat = CATEGORIES.get(key, {})
        jp_query = cat_jp.get(key, cat.get("name", ""))
        await state.update_data(
            category_id=cat.get("id"),
            category_name=cat.get("name", ""),
            query=jp_query
        )
        await callback.message.edit_text(f"📂 Категория: <b>{cat.get('name', '')}</b>", parse_mode="HTML")

    await callback.message.answer("📐 Укажи размер (или пропусти):", reply_markup=skip_keyboard())
    await state.set_state(SearchForm.entering_size)
    await callback.answer()


@router.message(SearchForm.entering_size)
async def process_size(message: Message, state: FSMContext):
    await state.update_data(size=message.text.strip())
    await message.answer("🏷 Выбери состояние:", reply_markup=condition_keyboard())
    await state.set_state(SearchForm.entering_condition)


@router.callback_query(SearchForm.entering_size, F.data == "skip")
async def skip_size(callback: CallbackQuery, state: FSMContext):
    await state.update_data(size=None)
    await callback.message.edit_text("📐 Размер: <b>любой</b>", parse_mode="HTML")
    await callback.message.answer("🏷 Выбери состояние:", reply_markup=condition_keyboard())
    await state.set_state(SearchForm.entering_condition)
    await callback.answer()


@router.callback_query(SearchForm.entering_condition, F.data.startswith("cond_"))
async def process_condition(callback: CallbackQuery, state: FSMContext):
    cond = callback.data.replace("cond_", "")
    condition = None if cond == "any" else cond
    cond_labels = {
        "new": "10/10 Новый", "like_new": "9/10 Почти новый",
        "good": "8/10 Хорошее", "fair": "6/10 Среднее",
        "poor": "4/10 Плохое", "any": "Любое"
    }
    await state.update_data(condition=condition)
    await callback.message.edit_text(f"🏷 Состояние: <b>{cond_labels.get(cond)}</b>", parse_mode="HTML")
    await callback.message.answer(
        "💰 Укажи диапазон цен (или пропусти):\n<i>Для Bunjang в вонах (KRW), для Japan в йенах, для Grailed в долларах</i>",
        reply_markup=skip_keyboard(), parse_mode="HTML"
    )
    await state.set_state(SearchForm.entering_price)
    await callback.answer()


@router.message(SearchForm.entering_price)
async def process_price(message: Message, state: FSMContext):
    text = message.text.strip()
    min_price, max_price = 0, 999999
    try:
        if "-" in text:
            parts = text.replace(" ", "").split("-")
            min_price = int(parts[0]) if parts[0] else 0
            max_price = int(parts[1]) if parts[1] else 999999
        else:
            max_price = int(text)
    except ValueError:
        await message.answer("⚠️ Неверный формат. Используй: 100-500")
        return
    await state.update_data(min_price=min_price, max_price=max_price)
    await message.answer("📅 Фильтр по дате:", reply_markup=date_keyboard())
    await state.set_state(SearchForm.entering_date)


@router.callback_query(SearchForm.entering_price, F.data == "skip")
async def skip_price(callback: CallbackQuery, state: FSMContext):
    await state.update_data(min_price=0, max_price=999999)
    await callback.message.edit_text("💰 Цена: <b>любая</b>", parse_mode="HTML")
    await callback.message.answer("📅 Фильтр по дате:", reply_markup=date_keyboard())
    await state.set_state(SearchForm.entering_date)
    await callback.answer()


@router.callback_query(SearchForm.entering_date, F.data.startswith("date_"))
async def process_date(callback: CallbackQuery, state: FSMContext):
    date_filter = callback.data.replace("date_", "")
    date_labels = {
        "1h": "⚡ Свежие (за час)", "24h": "За день",
        "72h": "За 3 дня", "7d": "За неделю", "any": "Любое время"
    }
    hours_map = {"1h": 1, "24h": 24, "72h": 72, "7d": 168, "any": None}
    hours = hours_map.get(date_filter)
    await state.update_data(date_hours=hours)
    await callback.message.edit_text(f"📅 Дата: <b>{date_labels.get(date_filter)}</b>", parse_mode="HTML")
    await callback.message.answer(
        "📊 Сколько показать?",
        reply_markup=results_count_keyboard(show_all=(hours == 1))
    )
    await state.set_state(None)
    await callback.answer()


@router.callback_query(F.data.startswith("count_"))
async def process_count(callback: CallbackQuery, state: FSMContext):
    raw = callback.data.replace("count_", "")
    count = 9999 if raw == "all" else int(raw)
    label = "все за час" if count == 9999 else str(count)
    await callback.answer()
    await state.update_data(result_count=count)
    await callback.message.edit_text(f"📊 Результатов: <b>{label}</b>", parse_mode="HTML")
    await _run_search(callback.message, state)


async def _run_search(message: Message, state: FSMContext):
    data = await state.get_data()
    platform = data.get("platform", "mercari")
    query = data.get("query", "")
    size = data.get("size")
    condition = data.get("condition")
    min_price = data.get("min_price", 0)
    max_price = data.get("max_price", 999999)
    date_hours = data.get("date_hours")
    result_count = data.get("result_count", 10)
    category_id = data.get("category_id")
    category_name = data.get("category_name", "")
    user_id = str(message.chat.id)
    _last_search[user_id] = data.copy()

    # Для Grailed сбрасываем историю при каждом поиске
    if platform == "grailed" and user_id in _shown_items:
        grailed_keys = [k for k in _shown_items[user_id] if k.startswith("grailed_")]
        for k in grailed_keys:
            _shown_items[user_id].discard(k)

    fetch_count = 100 if result_count == 9999 else min(result_count * 3, 100)
    display_name = category_name if category_name else query

    status_msg = await message.answer(
        f"🔍 Ищу <b>{display_name}</b>...",
        parse_mode="HTML"
    )

    yen_rate = await _get_yen_rate()
    krw_rate = await _get_krw_rate()
    usd_rate = await _get_usd_rate()

    tasks, labels = [], []
    if platform == "mercari":
        tasks.append(search_mercari(query, min_price, max_price, condition, size, fetch_count, PROXY_URL, category_id=category_id))
        labels.append("mercari")
    elif platform == "bunjang":
        tasks.append(search_bunjang(query, min_price, max_price, condition, size, fetch_count, category_id=category_id))
        labels.append("bunjang")
    elif platform == "grailed":
        tasks.append(search_grailed(query, min_price, max_price, condition, size, fetch_count, user_id=user_id))
        labels.append("grailed")

    results_list = await asyncio.gather(*tasks, return_exceptions=True)
    await status_msg.delete()
    await state.clear()

    if user_id not in _shown_items:
        _shown_items[user_id] = set()

    cutoff_time = datetime.now() - timedelta(hours=date_hours) if date_hours else None
    total = 0

    for label, results in zip(labels, results_list):
        if isinstance(results, Exception):
            await message.answer(f"❌ Ошибка: {results}")
            continue
        if not results:
            await message.answer(f"😔 <b>{PLATFORM_NAMES.get(label, label)}</b>: ничего не найдено", parse_mode="HTML")
            continue

        sent = 0
        for item in results:
            if user_id in _cancelled:
                _cancelled.discard(user_id)
                await message.answer("⛔ Поиск остановлен.", reply_markup=main_keyboard())
                return

            if result_count != 9999 and sent >= result_count:
                break

            item_uid = f"{label}_{item.goods_id if hasattr(item, 'goods_id') else item.id}"
            if item_uid in _shown_items[user_id]:
                continue

            if cutoff_time and hasattr(item, "created_at") and item.created_at:
                if item.created_at.replace(tzinfo=None) < cutoff_time:
                    continue

            _shown_items[user_id].add(item_uid)

            if label == "bunjang":
                rate = krw_rate
                currency = "₩"
            elif label == "grailed":
                rate = usd_rate
                currency = "$"
            else:
                rate = yen_rate
                currency = "¥"

            text = _format_item(item, label, rate, currency)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Открыть на сайте", url=item.url)],
            ])

            if item.image_url:
                try:
                    async with aiohttp.ClientSession() as img_session:
                        async with img_session.get(item.image_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                            if resp.status == 200:
                                from aiogram.types import BufferedInputFile
                                photo = BufferedInputFile(await resp.read(), filename="photo.jpg")
                                await message.answer_photo(photo=photo, caption=text, parse_mode="HTML", reply_markup=keyboard)
                                total += 1
                                sent += 1
                                continue
                except Exception:
                    pass

            await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
            total += 1
            sent += 1

    if total == 0:
        await message.answer(
            "😔 Ничего не найдено.\n/clear — сбросить историю\n/menu — меню"
        )
    else:
        await message.answer(
            f"✅ Показано: <b>{total}</b> позиций\n\n/menu — главное меню",
            parse_mode="HTML"
        )


def _format_item(item, platform: str, rate: float = 0.62, currency: str = "¥") -> str:
    platform_icons = {
        "mercari": "🇯🇵 Mercari",
        "bunjang": "🇰🇷 Bunjang",
        "grailed": "🇺🇸 Grailed",
    }


    rub_price = int(item.price * rate)
    lines = [
        f"<b>{platform_icons.get(platform, platform)}</b>",
        f"📦 <b>{item.name}</b>",
        f"💴 <b>{currency}{item.price:,} / ~{rub_price:,}₽</b>",
        f"🏷 Состояние: {item.condition}",
    ]
    if item.size:
        lines.append(f"📐 Размер: {item.size}")
    if hasattr(item, "brand") and item.brand:
        lines.append(f"🔖 Бренд: {item.brand}")
    if item.seller:
        lines.append(f"👤 Продавец: {item.seller}")
    if hasattr(item, "created_at") and item.created_at:
        dt = item.created_at.replace(tzinfo=None) if item.created_at.tzinfo else item.created_at
        lines.append(f"🕐 {format_date(dt)}")
    lines.append(f"📊 {item.status}")
    return "\n".join(lines)


@router.message(F.text.startswith("!"))
async def quick_search(message: Message, state: FSMContext):
    query_text = message.text[1:].strip()
    if not query_text:
        return
    await state.update_data(
        platform="mercari", query=query_text, size=None,
        condition=None, min_price=0, max_price=999999,
        date_hours=None, result_count=10, category_id=None
    )
    await _run_search(message, state)


@router.message(Command("repeat"))
async def cmd_repeat(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    if user_id not in _last_search or not _last_search[user_id]:
        await message.answer("❌ Нет предыдущего поиска. Используй /menu для начала.")
        return
    await message.answer("🔄 Повторяю последний поиск...")
    await state.update_data(**_last_search[user_id])
    await _run_search(message, state)
