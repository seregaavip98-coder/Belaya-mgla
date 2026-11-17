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
RESOURCE_NAMES = {
    "wood": "🪵 Дерево",
    "food": "🍖 Еда",
    "metal": "🔩 Металл",
    "fuel": "🔥 Топливо",
}
PRODUCTION_PER_HOUR = {"wood": 60, "food": 45, "metal": 20, "fuel": 25}
MAX_OFFLINE_HOURS = 12
BASE_STORAGE_CAPACITY = 5000

# Название здания, поле уровня в БД, производимый ресурс.
BUILDINGS = {
    "wood": ("🪵 Лесопилка", "sawmill_level", "wood"),
    "food": ("🌾 Ферма", "farm_level", "food"),
    "metal": ("⛏ Шахта", "mine_level", "metal"),
    "fuel": ("🔥 Топливная станция", "fuel_station_level", "fuel"),
    "warehouse": ("📦 Склад", "warehouse_level", None),
}

# Стоимость улучшения здания зависит от его текущего уровня.
def building_cost(building_id: str, level: int):
    costs = {
        "wood": {"wood": 300, "metal": 100, "food": 100},
        "food": {"wood": 250, "metal": 75, "food": 150},
        "metal": {"wood": 350, "food": 100, "fuel": 100},
        "fuel": {"wood": 300, "metal": 100, "food": 100},
        "warehouse": {"wood": 400, "metal": 150},
    }
    return {resource: amount * level for resource, amount in costs[building_id].items()}


def format_number(value: int):
    return f"{value:,}".replace(",", " ")


def storage_capacity(player):
    return BASE_STORAGE_CAPACITY * player["warehouse_level"]


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
            alliance TEXT,
            sawmill_level INTEGER NOT NULL DEFAULT 1,
            farm_level INTEGER NOT NULL DEFAULT 1,
            mine_level INTEGER NOT NULL DEFAULT 1,
            fuel_station_level INTEGER NOT NULL DEFAULT 1,
            warehouse_level INTEGER NOT NULL DEFAULT 1
        )""")

        # Безопасное добавление новых полей в уже существующую базу.
        cur = await db.execute("PRAGMA table_info(players)")
        existing_columns = {row[1] for row in await cur.fetchall()}
        new_columns = {
            "sawmill_level": "INTEGER NOT NULL DEFAULT 1",
            "farm_level": "INTEGER NOT NULL DEFAULT 1",
            "mine_level": "INTEGER NOT NULL DEFAULT 1",
            "fuel_station_level": "INTEGER NOT NULL DEFAULT 1",
            "warehouse_level": "INTEGER NOT NULL DEFAULT 1",
        }
        for column, definition in new_columns.items():
            if column not in existing_columns:
                await db.execute(f"ALTER TABLE players ADD COLUMN {column} {definition}")
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
        """, (
            user_id, username, name,
            START_RESOURCES["wood"], START_RESOURCES["food"],
            START_RESOURCES["metal"], START_RESOURCES["fuel"], now
        ))
        await db.commit()


async def refresh_player(player):
    now = time.time()
    elapsed = min(max(0, now - player["last_update"]), MAX_OFFLINE_HOURS * 3600)
    hours = elapsed / 3600
    furnace_multiplier = player["furnace_level"]
    levels = {
        "wood": player["sawmill_level"],
        "food": player["farm_level"],
        "metal": player["mine_level"],
        "fuel": player["fuel_station_level"],
    }
    capacity = storage_capacity(player)
    gains = {
        resource: int(PRODUCTION_PER_HOUR[resource] * hours * levels[resource] * furnace_multiplier)
        for resource in PRODUCTION_PER_HOUR
    }

    async with aiosqlite.connect(DB_PATH) as db:
        # Производство не превышает вместимость склада.
        await db.execute("""
        UPDATE players
        SET wood=MIN(wood+?, ?),
            food=MIN(food+?, ?),
            metal=MIN(metal+?, ?),
            fuel=MIN(fuel+?, ?),
            last_update=?
        WHERE user_id=?
        """, (
            gains["wood"], capacity,
            gains["food"], capacity,
            gains["metal"], capacity,
            gains["fuel"], capacity,
            now, player["user_id"]
        ))
        await db.commit()
    return await get_player(player["user_id"])


def main_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏰 Моя база", callback_data="base")
    kb.button(text="📦 Ресурсы", callback_data="resources")
    kb.button(text="🏗 Здания", callback_data="buildings")
    kb.button(text="🔥 Улучшить печь", callback_data="upgrade")
    kb.button(text="🤝 Альянс", callback_data="alliance")
    kb.button(text="🏆 Рейтинг", callback_data="rating")
    kb.adjust(2, 2, 2)
    return kb.as_markup()


def resources_text(p):
    capacity = storage_capacity(p)
    return (
        f"📦 <b>Запасы поселения</b>\n\n"
        f"🪵 Дерево: <b>{format_number(p['wood'])}</b>\n"
        f"🍖 Еда: <b>{format_number(p['food'])}</b>\n"
        f"🔩 Металл: <b>{format_number(p['metal'])}</b>\n"
        f"🔥 Топливо: <b>{format_number(p['fuel'])}</b>\n\n"
        f"📦 Вместимость склада: <b>{format_number(capacity)}</b> на каждый ресурс\n"
        f"<i>Ресурсы добываются автоматически, пока вас нет в игре. "
        f"Офлайн-производство ограничено 12 часами.</i>"
    )


def buildings_text(p):
    capacity = storage_capacity(p)
    return (
        "🏗 <b>Здания поселения</b>\n\n"
        f"🪵 Лесопилка — уровень <b>{p['sawmill_level']}</b>\n"
        f"🌾 Ферма — уровень <b>{p['farm_level']}</b>\n"
        f"⛏ Шахта — уровень <b>{p['mine_level']}</b>\n"
        f"🔥 Топливная станция — уровень <b>{p['fuel_station_level']}</b>\n"
        f"📦 Склад — уровень <b>{p['warehouse_level']}</b>\n"
        f"Вместимость: <b>{format_number(capacity)}</b> каждого ресурса\n\n"
        "Выбери здание, чтобы улучшить его. Чем выше уровень производственного "
        "здания, тем больше ресурсов оно добывает."
    )


def buildings_keyboard(p):
    kb = InlineKeyboardBuilder()
    buttons = [
        ("wood", "🪵 Улучшить лесопилку"),
        ("food", "🌾 Улучшить ферму"),
        ("metal", "⛏ Улучшить шахту"),
        ("fuel", "🔥 Улучшить топливную станцию"),
        ("warehouse", "📦 Улучшить склад"),
    ]
    for building_id, label in buttons:
        kb.button(text=label, callback_data=f"building:{building_id}")
    kb.button(text="⬅️ Назад", callback_data="base")
    kb.adjust(1)
    return kb.as_markup()


def cost_text(cost):
    names = {"wood": "🪵", "food": "🍖", "metal": "🔩", "fuel": "🔥"}
    return " · ".join(f"{names[key]} {format_number(value)}" for key, value in cost.items())


async def show_home(message: Message, player):
    player = await refresh_player(player)
    await message.answer(
        f"❄️ <b>БЕЛАЯ МГЛА: ВЫЖИВАНИЕ</b>\n"
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
    await create_player(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or "Выживший"
    )
    player = await get_player(message.from_user.id)
    await show_home(message, player)


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "❄️ <b>БЕЛАЯ МГЛА: ВЫЖИВАНИЕ — помощь</b>\n"
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
        await create_player(
            call.from_user.id,
            call.from_user.username or "",
            call.from_user.first_name or "Выживший"
        )
        p = await get_player(call.from_user.id)
    p = await refresh_player(p)
    await call.message.edit_text(
        f"❄️ <b>БЕЛАЯ МГЛА: ВЫЖИВАНИЕ</b>\n"
        f"🏰 Поселение №{p['user_id'] % 100000:05d}\n"
        f"🔥 Центральная печь: уровень {p['furnace_level']}\n"
        f"🌡 Температура: −35°C\n\n{resources_text(p)}",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )
    await call.answer()


@dp.callback_query(F.data == "resources")
async def cb_resources(call: CallbackQuery):
    p = await get_player(call.from_user.id)
    p = await refresh_player(p)
    await call.message.edit_text(
        resources_text(p), parse_mode="HTML", reply_markup=main_keyboard()
    )
    await call.answer("Запасы обновлены")


@dp.callback_query(F.data == "buildings")
async def cb_buildings(call: CallbackQuery):
    p = await get_player(call.from_user.id)
    p = await refresh_player(p)
    await call.message.edit_text(
        buildings_text(p),
        parse_mode="HTML",
        reply_markup=buildings_keyboard(p)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("building:"))
async def cb_building_upgrade(call: CallbackQuery):
    building_id = call.data.split(":", 1)[1]
    if building_id not in BUILDINGS:
        await call.answer("Неизвестное здание.", show_alert=True)
        return

    p = await get_player(call.from_user.id)
    if not p:
        await call.answer("Сначала запусти игру командой /start.", show_alert=True)
        return
    p = await refresh_player(p)

    building_name, level_column, produced_resource = BUILDINGS[building_id]
    level = p[level_column]
    cost = building_cost(building_id, level)

    missing = [
        RESOURCE_NAMES[key]
        for key, amount in cost.items()
        if p[key] < amount
    ]
    if missing:
        await call.answer(
            "Не хватает ресурсов: " + ", ".join(missing),
            show_alert=True
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""UPDATE players
                SET wood=wood-?, food=food-?, metal=metal-?, fuel=fuel-?,
                    {level_column}={level_column}+1
                WHERE user_id=?""",
            (
                cost.get("wood", 0),
                cost.get("food", 0),
                cost.get("metal", 0),
                cost.get("fuel", 0),
                call.from_user.id
            )
        )
        await db.commit()

    p = await get_player(call.from_user.id)
    p = await refresh_player(p)
    if building_id == "warehouse":
        result = (
            f"Новый уровень: <b>{p[level_column]}</b>\n"
            f"Вместимость каждого ресурса теперь: "
            f"<b>{format_number(storage_capacity(p))}</b>."
        )
    else:
        result = (
            f"Новый уровень: <b>{p[level_column]}</b>\n"
            f"Производство ресурса увеличено."
        )

    await call.message.edit_text(
        f"🏗 <b>{building_name} улучшена!</b>\n\n"
        f"{result}\n\n"
        f"💸 Потрачено: {cost_text(cost)}",
        parse_mode="HTML",
        reply_markup=buildings_keyboard(p)
    )
    await call.answer("Улучшение завершено!")


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
        UPDATE players
        SET wood=wood-?, metal=metal-?, fuel=fuel-?, furnace_level=furnace_level+1
        WHERE user_id=?
        """, (cost["wood"], cost["metal"], cost["fuel"], call.from_user.id))
        await db.commit()
    p = await get_player(call.from_user.id)
    await call.message.edit_text(
        f"🔥 <b>Центральная печь улучшена!</b>\n\n"
        f"Новый уровень: <b>{p['furnace_level']}</b>\n"
        f"Теперь печь ускоряет производство всех ресурсов.",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )
    await call.answer("Улучшение завершено!")


@dp.callback_query(F.data == "alliance")
async def cb_alliance(call: CallbackQuery):
    text = (
        "🤝 <b>Альянсы</b>\n\n"
        "Система альянсов появится в следующем обновлении. "
        "Здесь будут создание союза, вступление по тегу, казна и совместные события."
    )
    await call.message.edit_text(
        text, parse_mode="HTML", reply_markup=main_keyboard()
    )
    await call.answer()


@dp.callback_query(F.data == "rating")
async def cb_rating(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT name, furnace_level
            FROM players
            ORDER BY furnace_level DESC, wood+food+metal+fuel DESC
            LIMIT 10
        """)
        rows = await cur.fetchall()
    if not rows:
        text = "🏆 Пока нет игроков в рейтинге."
    else:
        text = "🏆 <b>Рейтинг выживших</b>\n\n"
        for i, row in enumerate(rows, 1):
            text += f"{i}. {row['name']} — печь ур. {row['furnace_level']}\n"
    await call.message.edit_text(
        text, parse_mode="HTML", reply_markup=main_keyboard()
    )
    await call.answer()


async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
