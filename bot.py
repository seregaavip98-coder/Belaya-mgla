import asyncio
import os
import time
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "game.db")

if not TOKEN:
    raise RuntimeError("Укажите BOT_TOKEN в файле .env")

bot = Bot(TOKEN)
dp = Dispatcher()

START_RESOURCES = {"wood": 500, "food": 300, "metal": 150, "fuel": 200}
RESOURCE_NAMES = {"wood": "🪵 Дерево", "food": "🍖 Еда", "metal": "🔩 Металл", "fuel": "🔥 Топливо"}
PRODUCTION_PER_HOUR = {"wood": 60, "food": 45, "metal": 20, "fuel": 25}
MAX_OFFLINE_HOURS = 12

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            name TEXT,
            wood INTEGER NOT NULL,
            food INTEGER NOT NULL,
            metal INTEGER NOT NULL,
            fuel INTEGER NOT NULL,
            furnace_level INTEGER NOT NULL DEFAULT 1,
            last_update REAL NOT NULL,
            alliance TEXT
        )""")
        await db.commit()

async def get_player(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM players WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def create_player(user_id: int, username: str, name: str):
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT OR IGNORE INTO players
        (user_id, username, name, wood, food, metal, fuel, furnace_level, last_update)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, (user_id, username, name, START_RESOURCES["wood"], START_RESOURCES["food"],
              START_RESOURCES["metal"], START_RESOURCES["fuel"], now))
        await db.commit()

async def refresh_player(player):
    now = time.time()
    elapsed = min(max(0, now - player["last_update"]), MAX_OFFLINE_HOURS * 3600)
    hours = elapsed / 3600
    mult = player["furnace_level"]
    gains = {k: int(PRODUCTION_PER_HOUR[k] * hours * mult) for k in PRODUCTION_PER_HOUR}
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE players SET wood=wood+?, food=food+?, metal=metal+?, fuel=fuel+?, last_update=?
        WHERE user_id=?
        """, (gains["wood"], gains["food"], gains["metal"], gains["fuel"], now, player["user_id"]))
        await db.commit()
    return await get_player(player["user_id"])

def main_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏰 Моя база", callback_data="base")
    kb.button(text="📦 Ресурсы", callback_data="resources")
    kb.button(text="🏗 Улучшить печь", callback_data="upgrade")
    kb.button(text="🤝 Альянс", callback_data="alliance")
    kb.button(text="🏆 Рейтинг", callback_data="rating")
    kb.adjust(2, 2, 1)
    return kb.as_markup()

def resources_text(p):
    return (
        f"📦 <b>Запасы поселения</b>\n\n"
        f"🪵 Дерево: <b>{p['wood']:,}</b>\n"
        f"🍖 Еда: <b>{p['food']:,}</b>\n"
        f"🔩 Металл: <b>{p['metal']:,}</b>\n"
        f"🔥 Топливо: <b>{p['fuel']:,}</b>\n\n"
        f"<i>Ресурсы добываются автоматически, пока вас нет в игре.</i>"
    ).replace(",", " ")

async def show_home(message: Message, player):
    player = await refresh_player(player)
    await message.answer(
        f"❄️ <b>ПОСЛЕДНИЙ ОЧАГ</b>\n"
        f"Добро пожаловать, {player['name']}!\n\n"
        f"🏰 Поселение №{player['user_id'] % 100000:05d}\n"
        f"🔥 Центральная печь: уровень {player['furnace_level']}\n"
        f"🌡 Температура: −35°C\n\n"
        f"{resources_text(player)}",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )

@dp.message(CommandStart())
async def start(message: Message):
    await create_player(message.from_user.id, message.from_user.username or "", message.from_user.first_name or "Выживший")
    player = await get_player(message.from_user.id)
    await show_home(message, player)

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "❄️ <b>ПОСЛЕДНИЙ ОЧАГ — помощь</b>\n"
        "/start — открыть игру\n"
        "/base — показать базу\n"
        "/resources — показать ресурсы\n\n"
        "Выберите действие кнопками под игровым экраном.",
        parse_mode="HTML"
    )

@dp.message(Command("base"))
async def base_cmd(message: Message):
    p = await get_player(message.from_user.id)
    if not p:
        await start(message)
    else:
        await show_home(message, p)

@dp.message(Command("resources"))
async def resources_cmd(message: Message):
    p = await get_player(message.from_user.id)
    if not p:
        await start(message)
        return
    p = await refresh_player(p)
    await message.answer(resources_text(p), parse_mode="HTML", reply_markup=main_keyboard())

@dp.callback_query(F.data == "base")
async def cb_base(call: CallbackQuery):
    p = await get_player(call.from_user.id)
    if not p:
        await create_player(call.from_user.id, call.from_user.username or "", call.from_user.first_name or "Выживший")
        p = await get_player(call.from_user.id)
    p = await refresh_player(p)
    await call.message.edit_text(
        f"❄️ <b>ПОСЛЕДНИЙ ОЧАГ</b>\n🏰 Поселение №{p['user_id'] % 100000:05d}\n"
        f"🔥 Центральная печь: уровень {p['furnace_level']}\n🌡 Температура: −35°C\n\n{resources_text(p)}",
        parse_mode="HTML", reply_markup=main_keyboard()
    )
    await call.answer()

@dp.callback_query(F.data == "resources")
async def cb_resources(call: CallbackQuery):
    p = await get_player(call.from_user.id)
    p = await refresh_player(p)
    await call.message.edit_text(resources_text(p), parse_mode="HTML", reply_markup=main_keyboard())
    await call.answer("Запасы обновлены")

@dp.callback_query(F.data == "upgrade")
async def cb_upgrade(call: CallbackQuery):
    p = await get_player(call.from_user.id)
    p = await refresh_player(p)
    level = p["furnace_level"]
    cost = {"wood": 250 * level, "metal": 100 * level, "fuel": 100 * level}
    if p["wood"] < cost["wood"] or p["metal"] < cost["metal"] or p["fuel"] < cost["fuel"]:
        await call.answer("Не хватает ресурсов для улучшения!", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE players SET wood=wood-?, metal=metal-?, fuel=fuel-?, furnace_level=furnace_level+1
        WHERE user_id=?
        """, (cost["wood"], cost["metal"], cost["fuel"], call.from_user.id))
        await db.commit()
    p = await get_player(call.from_user.id)
    await call.message.edit_text(
        f"🔥 <b>Центральная печь улучшена!</b>\n\n"
        f"Новый уровень: <b>{p['furnace_level']}</b>\n"
        f"Теперь производство ресурсов ускорено.",
        parse_mode="HTML", reply_markup=main_keyboard()
    )
    await call.answer("Улучшение завершено!")

@dp.callback_query(F.data == "alliance")
async def cb_alliance(call: CallbackQuery):
    p = await get_player(call.from_user.id)
    text = "🤝 <b>Альянсы</b>\n\nСистема альянсов появится в следующем обновлении. Здесь будут создание союза, вступление по тегу, казна и совместные события."
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=main_keyboard())
    await call.answer()

@dp.callback_query(F.data == "rating")
async def cb_rating(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT name, furnace_level FROM players ORDER BY furnace_level DESC, wood+food+metal+fuel DESC LIMIT 10")
        rows = await cur.fetchall()
    if not rows:
        text = "🏆 Пока нет игроков в рейтинге."
    else:
        text = "🏆 <b>Рейтинг выживших</b>\n\n"
        for i, row in enumerate(rows, 1):
            text += f"{i}. {row['name']} — печь ур. {row['furnace_level']}\n"
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=main_keyboard())
    await call.answer()

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
