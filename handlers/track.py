"""
Хендлер трекинга — отслеживание позиций.
"""
import asyncio
import json
import os
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command

from parsers.mercari import search_mercari
from parsers.app95 import search_95app
from config import TRACK_INTERVAL, PROXY_URL

router = Router()

TRACKS_FILE = "tracks.json"
_tracks: dict = {}
_seen_ids: dict = {}


def load_tracks():
    global _tracks
    if os.path.exists(TRACKS_FILE):
        with open(TRACKS_FILE) as f:
            _tracks = json.load(f)


def save_tracks():
    with open(TRACKS_FILE, "w") as f:
        json.dump(_tracks, f, ensure_ascii=False, indent=2)


load_tracks()


class TrackForm(StatesGroup):
    entering_query = State()
    entering_params = State()


@router.message(Command("track"))
async def cmd_track(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    user_tracks = _tracks.get(user_id, [])

    if user_tracks:
        text = "📌 <b>Твои трекинги:</b>\n\n"
        for i, t in enumerate(user_tracks, 1):
            text += (
                f"{i}. <b>{t['query']}</b>\n"
                f"   Платформа: {t.get('platform', 'все')}\n"
                f"   Размер: {t.get('size', 'любой')}\n"
                f"   Цена: {t.get('min_price', 0)}–{t.get('max_price', '∞')}\n\n"
            )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить трекинг", callback_data="track_add")],
            [InlineKeyboardButton(text="🗑 Удалить все", callback_data="track_clear")],
        ])
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(
            "📌 <b>Трекинг позиций</b>\n\n"
            "Нет активных отслеживаний.\n"
            "Добавь позицию — бот будет уведомлять при появлении новых товаров.\n\n"
            "Введи запрос для трекинга:",
            parse_mode="HTML"
        )
        await state.set_state(TrackForm.entering_query)


@router.callback_query(F.data == "track_add")
async def start_add_track(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введи запрос для нового трекинга:")
    await state.set_state(TrackForm.entering_query)
    await callback.answer()


@router.callback_query(F.data == "track_clear")
async def clear_tracks(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    _tracks.pop(user_id, None)
    save_tracks()
    await callback.message.edit_text("✅ Все трекинги удалены.")
    await callback.answer()


@router.message(TrackForm.entering_query)
async def process_track_query(message: Message, state: FSMContext):
    await state.update_data(query=message.text.strip())
    await message.answer(
        "⚙️ Параметры (опционально, или /skip):\n"
        "Формат: <code>платформа размер мин-цена:макс-цена состояние</code>\n\n"
        "Примеры:\n"
        "<code>mercari 27cm 5000:20000 like_new</code>\n"
        "<code>all 42</code>\n"
        "<code>95app US9 new</code>\n\n"
        "Платформы: mercari, 95app, all\n"
        "Состояния: new, like_new, good, fair, poor",
        parse_mode="HTML"
    )
    await state.set_state(TrackForm.entering_params)


@router.message(TrackForm.entering_params)
async def process_track_params(message: Message, state: FSMContext):
    data = await state.get_data()
    query = data.get("query", "")

    platform = "all"
    size = None
    min_price = 0
    max_price = 999999
    condition = None

    if message.text and message.text.strip() != "/skip":
        parts = message.text.strip().split()
        for part in parts:
            if part in ("mercari", "95app", "all"):
                platform = part
            elif part in ("new", "like_new", "good", "fair", "poor"):
                condition = part
            elif ":" in part:
                try:
                    mn, mx = part.split(":")
                    min_price = int(mn) if mn else 0
                    max_price = int(mx) if mx else 999999
                except:
                    pass
            else:
                size = part

    user_id = str(message.from_user.id)
    if user_id not in _tracks:
        _tracks[user_id] = []

    track = {
        "query": query,
        "platform": platform,
        "size": size,
        "condition": condition,
        "min_price": min_price,
        "max_price": max_price,
    }

    _tracks[user_id].append(track)
    save_tracks()

    await state.clear()
    await message.answer(
        f"✅ <b>Трекинг добавлен!</b>\n\n"
        f"🔍 Запрос: <b>{query}</b>\n"
        f"🌏 Платформа: {platform}\n"
        f"📐 Размер: {size or 'любой'}\n"
        f"💰 Цена: {min_price}–{max_price if max_price < 999999 else '∞'}\n"
        f"🏷 Состояние: {condition or 'любое'}\n\n"
        f"Бот проверяет каждые {TRACK_INTERVAL // 60} мин.",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("track_"))
async def quick_track(callback: CallbackQuery):
    parts = callback.data.split("_", 2)
    if len(parts) < 3:
        await callback.answer("Ошибка")
        return

    platform = parts[1]
    item_id = parts[2]
    user_id = str(callback.from_user.id)

    track_key = f"{user_id}_{platform}_{item_id}"
    if track_key not in _seen_ids:
        _seen_ids[track_key] = set()
    _seen_ids[track_key].add(item_id)

    await callback.answer("📌 Добавлено в отслеживание!", show_alert=True)


async def tracking_loop(bot: Bot):
    while True:
        await asyncio.sleep(TRACK_INTERVAL)

        for user_id, tracks in list(_tracks.items()):
            for track in tracks:
                try:
                    await check_track(bot, user_id, track)
                except Exception as e:
                    print(f"[Track] Ошибка для {user_id}: {e}")


async def check_track(bot: Bot, user_id: str, track: dict):
    query = track["query"]
    platform = track.get("platform", "all")
    size = track.get("size")
    condition = track.get("condition")
    min_price = track.get("min_price", 0)
    max_price = track.get("max_price", 999999)

    track_key = f"{user_id}_{platform}_{query}"

    if track_key not in _seen_ids:
        _seen_ids[track_key] = set()

    parsers = []
    if platform in ("mercari", "all"):
        parsers.append(("mercari", search_mercari(query, min_price, max_price, condition, size, 20, PROXY_URL)))
    if platform in ("95app", "all"):
        parsers.append(("95app", search_95app(query, min_price, max_price, condition, size, 20, PROXY_URL)))

    results = await asyncio.gather(*[p[1] for p in parsers], return_exceptions=True)

    for (plat, _), items in zip(parsers, results):
        if isinstance(items, Exception) or not items:
            continue

        for item in items:
            item_uid = f"{plat}_{item.id}"
            if item_uid not in _seen_ids[track_key]:
                _seen_ids[track_key].add(item_uid)

                if len(_seen_ids[track_key]) <= len(items):
                    continue

                currency = "¥"
                text = (
                    f"🔔 <b>Новый товар!</b>\n\n"
                    f"🔍 Трек: <b>{query}</b>\n"
                    f"📦 {item.name}\n"
                    f"💴 <b>{currency}{item.price:,}</b>\n"
                    f"🏷 {item.condition}\n"
                )
                if item.size:
                    text += f"📐 {item.size}\n"

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔗 Открыть", url=item.url)]
                ])

                try:
                    await bot.send_message(
                        int(user_id),
                        text,
                        parse_mode="HTML",
                        reply_markup=keyboard
                    )
                except Exception as e:
                    print(f"[Track] Не смог отправить {user_id}: {e}")
