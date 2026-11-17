import asyncio
import os
import time
import html
import random
import sqlite3
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

_db_setting = os.getenv("DB_PATH", "game.db")
DB_PATH = (
    _db_setting
    if os.path.isabs(_db_setting)
    else os.path.join(DATA_DIR, os.path.basename(_db_setting))
)
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
# Перенос старой базы в постоянное хранилище Bothost
LEGACY_DB_PATH = "/app/game.db"

if (
    os.path.abspath(LEGACY_DB_PATH) != os.path.abspath(DB_PATH)
    and os.path.isfile(LEGACY_DB_PATH)
    and not os.path.exists(DB_PATH)
):
    print("Переносим старую базу данных...")

    with sqlite3.connect(LEGACY_DB_PATH) as old_db:
        with sqlite3.connect(DB_PATH) as new_db:
            old_db.backup(new_db)

    print("Старая база успешно перенесена!")
if not TOKEN:
    raise RuntimeError("Укажите BOT_TOKEN в переменных окружения Bothost")

bot = Bot(TOKEN)
dp = Dispatcher()

START_RESOURCES = {"wood": 500, "food": 300, "metal": 150, "fuel": 200}
RESOURCE_NAMES = {"wood": "🪵 Дерево", "food": "🍖 Еда", "metal": "🔩 Металл", "fuel": "🔥 Топливо"}
RESOURCE_ICONS = {"wood": "🪵", "food": "🍖", "metal": "🔩", "fuel": "🔥"}
PRODUCTION_PER_HOUR = {"wood": 60, "food": 45, "metal": 20, "fuel": 25}
MAX_OFFLINE_HOURS = 12
BASE_STORAGE_CAPACITY = 5000

BUILDINGS = {
    "wood": ("🪵 Лесопилка", "sawmill_level", "wood", "Добывает древесину для строительства и ремонта."),
    "food": ("🌾 Ферма", "farm_level", "food", "Производит провизию для экспедиций и выживания."),
    "metal": ("⛏ Шахта", "mine_level", "metal", "Добывает металл для укреплений и оборудования."),
    "fuel": ("🔥 Топливная станция", "fuel_station_level", "fuel", "Производит топливо для печи и экспедиций."),
    "warehouse": ("📦 Склад", "warehouse_level", None, "Увеличивает максимальный запас каждого ресурса."),
}
BUILDING_BASE_COSTS = {
    "wood": {"wood": 300, "metal": 100, "food": 100},
    "food": {"wood": 250, "metal": 75, "food": 150},
    "metal": {"wood": 350, "food": 100, "fuel": 100},
    "fuel": {"wood": 300, "metal": 100, "food": 100},
    "warehouse": {"wood": 400, "metal": 150},
}

QUEST_TEMPLATES = [
    ("collect", "Собери 500 древесины", "wood", 500, 120, 80),
    ("collect", "Собери 400 еды", "food", 400, 100, 80),
    ("collect", "Собери 150 металла", "metal", 150, 100, 100),
    ("collect", "Собери 150 топлива", "fuel", 150, 100, 100),
    ("upgrade", "Улучши любое здание 1 раз", "building", 1, 150, 120),
    ("expedition", "Заверши 1 экспедицию", "expedition", 1, 120, 100),
]
HEROES = [
    ("mira", "🧭 Мира", "Разведчица", "Повышает награду экспедиций на 5%."),
    ("boris", "🛡 Борис", "Защитник", "Укрепляет оборону поселения."),
    ("lev", "🔧 Лев", "Инженер", "Помогает в развитии базы."),
]
EXPEDITION_OPTIONS = {
    "30": (1800, 100, 60, 30, 10),
    "60": (3600, 220, 120, 70, 20),
    "180": (10800, 650, 350, 200, 50),
}

def fmt(n):
    return f"{int(n):,}".replace(",", " ")

def day_key():
    return time.strftime("%Y-%m-%d", time.gmtime())

def storage_capacity(p):
    return BASE_STORAGE_CAPACITY * max(1, p["warehouse_level"])

def building_cost(building_id, level):
    return {k: v * level for k, v in BUILDING_BASE_COSTS[building_id].items()}

def cost_text(cost):
    return " · ".join(f"{RESOURCE_ICONS[k]} {fmt(v)}" for k, v in cost.items())

async def columns(db, table):
    cur = await db.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in await cur.fetchall()}

async def add_column(db, table, name, definition):
    existing = await columns(db, table)
    if name not in existing:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY, username TEXT, name TEXT,
            wood INTEGER NOT NULL DEFAULT 500, food INTEGER NOT NULL DEFAULT 300,
            metal INTEGER NOT NULL DEFAULT 150, fuel INTEGER NOT NULL DEFAULT 200,
            furnace_level INTEGER NOT NULL DEFAULT 1, last_update REAL NOT NULL,
            alliance TEXT, sawmill_level INTEGER NOT NULL DEFAULT 1,
            farm_level INTEGER NOT NULL DEFAULT 1, mine_level INTEGER NOT NULL DEFAULT 1,
            fuel_station_level INTEGER NOT NULL DEFAULT 1, warehouse_level INTEGER NOT NULL DEFAULT 1
        )""")
        migrations = {
            "xp": "INTEGER NOT NULL DEFAULT 0", "level": "INTEGER NOT NULL DEFAULT 1",
            "wall_level": "INTEGER NOT NULL DEFAULT 1", "tower_level": "INTEGER NOT NULL DEFAULT 1",
            "crystals": "INTEGER NOT NULL DEFAULT 0", "vip_until": "REAL NOT NULL DEFAULT 0",
            "daily_streak": "INTEGER NOT NULL DEFAULT 0", "last_daily": "TEXT",
        }
        for name, definition in migrations.items():
            await add_column(db, "players", name, definition)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS daily_quests (
            user_id INTEGER NOT NULL, quest_day TEXT NOT NULL, slot INTEGER NOT NULL,
            kind TEXT NOT NULL, target TEXT NOT NULL, amount INTEGER NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0, reward_xp INTEGER NOT NULL,
            reward_crystals INTEGER NOT NULL, claimed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, quest_day, slot)
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS expeditions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            started_at REAL NOT NULL, duration INTEGER NOT NULL,
            reward_wood INTEGER NOT NULL, reward_food INTEGER NOT NULL,
            reward_metal INTEGER NOT NULL, reward_fuel INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS player_heroes (
            user_id INTEGER NOT NULL, hero_id TEXT NOT NULL, level INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(user_id, hero_id)
        )""")
        await db.commit()

async def get_player(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM players WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

async def create_player(user_id, username, name):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT OR IGNORE INTO players(user_id,username,name,wood,food,metal,fuel,furnace_level,last_update)
        VALUES(?,?,?,?,?,?,?,1,?)
        """, (user_id, username, name, START_RESOURCES["wood"], START_RESOURCES["food"],
              START_RESOURCES["metal"], START_RESOURCES["fuel"], time.time()))
        for hero_id, *_ in HEROES:
            await db.execute("INSERT OR IGNORE INTO player_heroes(user_id,hero_id,level) VALUES(?,?,1)",
                             (user_id, hero_id))
        await db.commit()

async def add_xp(user_id, amount):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE players SET xp=xp+? WHERE user_id=?", (amount, user_id))
        await db.execute("UPDATE players SET level=1+(xp/500) WHERE user_id=?", (user_id,))
        await db.commit()

async def refresh_player(player):
    now = time.time()
    elapsed = min(max(0, now - player["last_update"]), MAX_OFFLINE_HOURS * 3600)
    hours = elapsed / 3600
    levels = {"wood": player["sawmill_level"], "food": player["farm_level"],
              "metal": player["mine_level"], "fuel": player["fuel_station_level"]}
    cap = storage_capacity(player)
    gains = {r: int(PRODUCTION_PER_HOUR[r] * hours * levels[r] * player["furnace_level"])
             for r in PRODUCTION_PER_HOUR}
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE players SET wood=MIN(wood+?,?), food=MIN(food+?,?), metal=MIN(metal+?,?),
        fuel=MIN(fuel+?,?), last_update=? WHERE user_id=?
        """, (gains["wood"],cap,gains["food"],cap,gains["metal"],cap,gains["fuel"],cap,now,player["user_id"]))
        await db.commit()
    # Засчитываем произведённые ресурсы в ежедневные задания.
    await ensure_daily_quests(player["user_id"])
    for resource, amount in gains.items():
        if amount > 0:
            await quest_progress(player["user_id"], "collect", resource, amount)
    return await get_player(player["user_id"])

async def grant_resources(user_id, rewards):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT warehouse_level FROM players WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        cap = BASE_STORAGE_CAPACITY * (row[0] if row else 1)
        for r, amount in rewards.items():
            if r in RESOURCE_NAMES:
                await db.execute(f"UPDATE players SET {r}=MIN({r}+?,?) WHERE user_id=?", (int(amount), cap, user_id))
        await db.commit()

def main_keyboard():
    kb = InlineKeyboardBuilder()
    for label, data in [
        ("🏰 Моя база","base"),("📦 Ресурсы","resources"),
        ("🏗 Здания","buildings"),("🔥 Центральная печь","furnace"),
        ("📜 Задания","quests"),("🧭 Экспедиции","expeditions"),
        ("🦸 Герои","heroes"),("🛡 Оборона","defense"),
        ("🎁 Награда дня","daily"),("💎 Магазин","shop"),
        ("🤝 Альянс","alliance"),("🏆 Рейтинг","rating")
    ]:
        kb.button(text=label, callback_data=data)
    kb.adjust(2,2,2,2,2,2)
    return kb.as_markup()

def resources_text(p):
    return ("📦 <b>Запасы поселения</b>\n\n"
            + "\n".join(f"{RESOURCE_NAMES[r]}: <b>{fmt(p[r])}</b>" for r in RESOURCE_NAMES)
            + f"\n\n📦 Склад: <b>{fmt(storage_capacity(p))}</b> каждого ресурса"
            + "\n<i>Офлайн-производство начисляется максимум за 12 часов.</i>")

def home_text(p):
    return (f"❄️ <b>БЕЛАЯ МГЛА: ВЫЖИВАНИЕ</b>\n"
            f"🏰 Поселение №{p['user_id'] % 100000:05d}\n"
            f"👤 {html.escape(p['name'])} · уровень {p['level']}\n"
            f"⭐ Опыт: {p['xp']} / {p['level']*500}\n"
            f"🔥 Центральная печь: ур. {p['furnace_level']}\n"
            f"🌡 Температура: −35°C\n\n{resources_text(p)}")

def buildings_keyboard():
    kb = InlineKeyboardBuilder()
    for key, (name, *_rest) in BUILDINGS.items():
        kb.button(text=name, callback_data=f"b:view:{key}")
    kb.button(text="⬅️ В меню", callback_data="base")
    kb.adjust(1)
    return kb.as_markup()

def building_card(p, key):
    name, col, resource, description = BUILDINGS[key]
    level = p[col]
    cost = building_cost(key, level)
    if resource:
        current = PRODUCTION_PER_HOUR[resource] * level * p["furnace_level"]
        next_rate = PRODUCTION_PER_HOUR[resource] * (level+1) * p["furnace_level"]
        effect = f"Сейчас: <b>{fmt(current)}/ч</b>\nПосле улучшения: <b>{fmt(next_rate)}/ч</b>"
    else:
        effect = f"Сейчас: <b>{fmt(storage_capacity(p))}</b>\nПосле улучшения: <b>{fmt(BASE_STORAGE_CAPACITY*(level+1))}</b>"
    lines = []
    enough = True
    for r, amount in cost.items():
        ok = p[r] >= amount
        enough = enough and ok
        lines.append(f"{'✅' if ok else '❌'} {RESOURCE_NAMES[r]}: {fmt(p[r])} / {fmt(amount)}")
    kb = InlineKeyboardBuilder()
    if enough:
        kb.button(text="⬆️ Улучшить здание", callback_data=f"b:up:{key}")
    kb.button(text="⬅️ К зданиям", callback_data="buildings")
    kb.adjust(1)
    return (f"{name}\n\n{description}\n\nУровень: <b>{level}</b> → <b>{level+1}</b>\n"
            f"{effect}\n\n<b>Стоимость улучшения:</b>\n" + "\n".join(lines)
            + ("\n\n✅ Ресурсов достаточно." if enough else "\n\n❗ Нужно накопить недостающие ресурсы."), kb.as_markup())

async def ensure_daily_quests(user_id):
    today = day_key()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM daily_quests WHERE user_id=? AND quest_day=?", (user_id,today))
        count = (await cur.fetchone())[0]
        if count == 0:
            picks = random.sample(QUEST_TEMPLATES, 3)
            for slot, q in enumerate(picks):
                kind, title, target, amount, xp, crystals = q
                await db.execute("""INSERT INTO daily_quests
                (user_id,quest_day,slot,kind,target,amount,reward_xp,reward_crystals)
                VALUES(?,?,?,?,?,?,?,?)""", (user_id,today,slot,kind,target,amount,xp,crystals))
        await db.commit()

async def quest_progress(user_id, kind, target, amount):
    today = day_key()
    async with aiosqlite.connect(DB_PATH) as db:
        if kind == "collect":
            await db.execute("""UPDATE daily_quests SET progress=MIN(amount,progress+?)
            WHERE user_id=? AND quest_day=? AND kind=? AND target=?""", (amount,user_id,today,kind,target))
        else:
            await db.execute("""UPDATE daily_quests SET progress=MIN(amount,progress+?)
            WHERE user_id=? AND quest_day=? AND kind=?""", (amount,user_id,today,kind))
        await db.commit()

async def quests_screen(call):
    await ensure_daily_quests(call.from_user.id)
    today = day_key()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM daily_quests WHERE user_id=? AND quest_day=? ORDER BY slot",
                               (call.from_user.id,today))
        rows = await cur.fetchall()
    text = "📜 <b>Ежедневные задания</b>\nОбновление каждый день по UTC.\n\n"
    kb = InlineKeyboardBuilder()
    for row in rows:
        title = row["target"]
        if row["kind"] == "collect":
            title = f"Собери {row['amount']} {RESOURCE_NAMES[row['target']]}"
        elif row["kind"] == "upgrade":
            title = "Улучши любое здание 1 раз"
        else:
            title = "Заверши 1 экспедицию"
        text += (f"{'✅' if row['progress'] >= row['amount'] else '▫️'} <b>{title}</b>\n"
                 f"Прогресс: {row['progress']}/{row['amount']} · Награда: {row['reward_xp']} XP, "
                 f"{row['reward_crystals']} 💎\n")
        if row["progress"] >= row["amount"] and not row["claimed"]:
            kb.button(text=f"🎁 Забрать награду #{row['slot']+1}", callback_data=f"q:claim:{row['slot']}")
    kb.button(text="⬅️ В меню", callback_data="base")
    kb.adjust(1)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
    await call.answer()

async def active_expedition(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM expeditions WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

def expeditions_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="🧭 Короткая · 30 минут", callback_data="e:start:30")
    kb.button(text="🌲 Обычная · 1 час", callback_data="e:start:60")
    kb.button(text="🏔 Дальняя · 3 часа", callback_data="e:start:180")
    kb.button(text="⬅️ В меню", callback_data="base")
    kb.adjust(1)
    return kb.as_markup()

async def expedition_screen(call):
    exp = await active_expedition(call.from_user.id)
    if exp:
        remaining = max(0, int(exp["started_at"]+exp["duration"]-time.time()))
        if remaining:
            mins, secs = divmod(remaining,60)
            text = f"🧭 <b>Экспедиция продолжается</b>\n\nДо возвращения: <b>{mins} мин. {secs} сек.</b>\nНаграда будет доступна после возвращения."
            kb = InlineKeyboardBuilder()
            kb.button(text="⬅️ В меню", callback_data="base")
        else:
            text = ("🧭 <b>Отряд вернулся!</b>\nНажми «Забрать награду».\n\n"
                    f"🪵 {exp['reward_wood']} · 🍖 {exp['reward_food']} · 🔩 {exp['reward_metal']} · 🔥 {exp['reward_fuel']}")
            kb = InlineKeyboardBuilder()
            kb.button(text="🎁 Забрать награду", callback_data=f"e:claim:{exp['id']}")
            kb.button(text="⬅️ В меню", callback_data="base")
        kb.adjust(1)
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await call.message.edit_text("🧭 <b>Экспедиции</b>\n\nОтправляй отряд за ресурсами. Пока отряд в пути, новую экспедицию начать нельзя.\n\nВыбери длительность:", parse_mode="HTML", reply_markup=expeditions_keyboard())
    await call.answer()

async def heroes_screen(call):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT hero_id,level FROM player_heroes WHERE user_id=? ORDER BY hero_id", (call.from_user.id,))
        owned = {r["hero_id"]:r["level"] for r in await cur.fetchall()}
    text = "🦸 <b>Герои поселения</b>\n\n"
    for hid, name, role, desc in HEROES:
        text += f"{name} · ур. {owned.get(hid,1)}\n<i>{role}. {desc}</i>\n\n"
    text += "Герои открыты с начала игры. Улучшение героев появится в следующем этапе баланса."
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В меню", callback_data="base")
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
    await call.answer()

def defense_screen(p):
    power = p["wall_level"]*100 + p["tower_level"]*150
    kb = InlineKeyboardBuilder()
    kb.button(text="🧱 Улучшить стены", callback_data="d:wall")
    kb.button(text="🏹 Улучшить башню", callback_data="d:tower")
    kb.button(text="⬅️ В меню", callback_data="base")
    kb.adjust(1)
    text = (f"🛡 <b>Оборона поселения</b>\n\n"
            f"🧱 Стены: уровень {p['wall_level']}\n"
            f"🏹 Башня: уровень {p['tower_level']}\n"
            f"⚔️ Защита поселения: <b>{power}</b>\n\n"
            "Укрепления готовят поселение к будущим атакам морозных тварей.")
    return text, kb.as_markup()

def shop_screen(p):
    text = ("💎 <b>Магазин поселения</b>\n\n"
            f"Твой баланс: <b>{p['crystals']} 💎</b>\n\n"
            "Кристаллы пока выдаются за ежедневные задания и награды.\n"
            "Платёжная система ещё не подключена. Никакие реальные деньги сейчас не списываются.\n\n"
            "🎁 Бесплатный стартовый набор: 100 кристаллов можно получить один раз.")
    kb = InlineKeyboardBuilder()
    kb.button(text="🎁 Получить стартовый набор", callback_data="shop:starter")
    kb.button(text="⬅️ В меню", callback_data="base")
    kb.adjust(1)
    return text, kb.as_markup()

@dp.message(CommandStart())
async def start(message: Message):
    await create_player(message.from_user.id, message.from_user.username or "", message.from_user.first_name or "Выживший")
    p = await refresh_player(await get_player(message.from_user.id))
    await message.answer(home_text(p), parse_mode="HTML", reply_markup=main_keyboard())

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer("❄️ <b>Помощь</b>\n/start — открыть игру\n/base — база\n/resources — ресурсы\n\nВсе игровые действия доступны через кнопки.", parse_mode="HTML")

@dp.message(Command("base"))
async def base_cmd(message: Message):
    p = await get_player(message.from_user.id)
    if not p:
        await start(message)
    else:
        p = await refresh_player(p)
        await message.answer(home_text(p), parse_mode="HTML", reply_markup=main_keyboard())

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
    await call.message.edit_text(home_text(p), parse_mode="HTML", reply_markup=main_keyboard())
    await call.answer()

@dp.callback_query(F.data == "resources")
async def cb_resources(call: CallbackQuery):
    p = await refresh_player(await get_player(call.from_user.id))
    await call.message.edit_text(resources_text(p), parse_mode="HTML", reply_markup=main_keyboard())
    await call.answer("Запасы обновлены")

@dp.callback_query(F.data == "buildings")
async def cb_buildings(call: CallbackQuery):
    p = await refresh_player(await get_player(call.from_user.id))
    text = "🏗 <b>Здания поселения</b>\n\n"
    for key,(name,col,res,desc) in BUILDINGS.items():
        if res:
            rate = PRODUCTION_PER_HOUR[res]*p[col]*p["furnace_level"]
            text += f"{name} — ур. <b>{p[col]}</b> · {fmt(rate)}/ч\n"
        else:
            text += f"{name} — ур. <b>{p[col]}</b> · {fmt(storage_capacity(p))} места\n"
    text += "\nВыбери здание, чтобы увидеть стоимость и эффект улучшения."
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=buildings_keyboard())
    await call.answer()

@dp.callback_query(F.data.startswith("b:view:"))
async def cb_building_view(call: CallbackQuery):
    key = call.data.split(":")[2]
    if key not in BUILDINGS:
        await call.answer("Неизвестное здание", show_alert=True); return
    p = await refresh_player(await get_player(call.from_user.id))
    text, kb = building_card(p,key)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("b:up:"))
async def cb_building_upgrade(call: CallbackQuery):
    key = call.data.split(":")[2]
    if key not in BUILDINGS:
        await call.answer("Неизвестное здание", show_alert=True); return
    p = await refresh_player(await get_player(call.from_user.id))
    name,col,resource,desc = BUILDINGS[key]
    level = p[col]; cost = building_cost(key,level)
    if any(p[r] < n for r,n in cost.items()):
        await call.answer("Не хватает ресурсов. Посмотри требования в карточке здания.", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"""UPDATE players SET wood=wood-?,food=food-?,metal=metal-?,fuel=fuel-?,{col}={col}+1 WHERE user_id=?""",
                         (cost.get("wood",0),cost.get("food",0),cost.get("metal",0),cost.get("fuel",0),call.from_user.id))
        await db.commit()
    await add_xp(call.from_user.id,50)
    await quest_progress(call.from_user.id,"upgrade","building",1)
    p = await get_player(call.from_user.id)
    text,kb = building_card(p,key)
    await call.message.edit_text(f"✅ <b>{name} улучшена!</b>\nПотрачено: {cost_text(cost)}\nПолучено: 50 XP\n\n{text}", parse_mode="HTML", reply_markup=kb)
    await call.answer("Улучшение завершено!")

@dp.callback_query(F.data == "furnace")
@dp.callback_query(F.data == "upgrade")
async def cb_furnace(call: CallbackQuery):
    p = await refresh_player(await get_player(call.from_user.id))
    level = p["furnace_level"]
    cost = {"wood":250*level,"metal":100*level,"fuel":100*level}
    enough = all(p[r] >= n for r,n in cost.items())
    kb = InlineKeyboardBuilder()
    if enough: kb.button(text="⬆️ Улучшить печь", callback_data="furnace:up")
    kb.button(text="⬅️ В меню", callback_data="base"); kb.adjust(1)
    lines = "\n".join(f"{'✅' if p[r]>=n else '❌'} {RESOURCE_NAMES[r]}: {fmt(p[r])}/{fmt(n)}" for r,n in cost.items())
    await call.message.edit_text(f"🔥 <b>Центральная печь</b>\n\nУровень: {level} → {level+1}\nУскоряет производство всех ресурсов.\n\n<b>Требуется:</b>\n{lines}", parse_mode="HTML", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data == "furnace:up")
async def cb_furnace_up(call: CallbackQuery):
    p = await get_player(call.from_user.id); level = p["furnace_level"]
    cost = {"wood":250*level,"metal":100*level,"fuel":100*level}
    if any(p[r]<n for r,n in cost.items()):
        await call.answer("Не хватает ресурсов",show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE players SET wood=wood-?,metal=metal-?,fuel=fuel-?,furnace_level=furnace_level+1 WHERE user_id=?",
                         (cost["wood"],cost["metal"],cost["fuel"],call.from_user.id)); await db.commit()
    await add_xp(call.from_user.id,75)
    await quest_progress(call.from_user.id,"upgrade","building",1)
    p=await get_player(call.from_user.id)
    await call.message.edit_text(f"🔥 Печь улучшена до уровня <b>{p['furnace_level']}</b>!\nПолучено 75 XP.",parse_mode="HTML",reply_markup=main_keyboard())
    await call.answer("Готово!")

@dp.callback_query(F.data == "quests")
async def cb_quests(call: CallbackQuery):
    await quests_screen(call)

@dp.callback_query(F.data.startswith("q:claim:"))
async def cb_quest_claim(call: CallbackQuery):
    slot = int(call.data.split(":")[2]); today = day_key()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        cur=await db.execute("SELECT * FROM daily_quests WHERE user_id=? AND quest_day=? AND slot=?",(call.from_user.id,today,slot))
        q=await cur.fetchone()
        if not q or q["progress"]<q["amount"] or q["claimed"]:
            await call.answer("Награда недоступна",show_alert=True); return
        await db.execute("UPDATE daily_quests SET claimed=1 WHERE user_id=? AND quest_day=? AND slot=?",(call.from_user.id,today,slot))
        await db.execute("UPDATE players SET crystals=crystals+? WHERE user_id=?",(q["reward_crystals"],call.from_user.id))
        await db.commit()
    await add_xp(call.from_user.id,q["reward_xp"])
    await quests_screen(call)
    await call.answer(f"Получено {q['reward_xp']} XP и {q['reward_crystals']} 💎")

@dp.callback_query(F.data == "expeditions")
async def cb_expeditions(call: CallbackQuery):
    await expedition_screen(call)

@dp.callback_query(F.data.startswith("e:start:"))
async def cb_expedition_start(call: CallbackQuery):
    minutes=int(call.data.split(":")[2])
    if minutes not in EXPEDITION_OPTIONS:
        await call.answer("Неизвестная экспедиция",show_alert=True); return
    if await active_expedition(call.from_user.id):
        await call.answer("У тебя уже есть активная экспедиция",show_alert=True); return
    p=await refresh_player(await get_player(call.from_user.id))
    duration,wood,food,metal,fuel=EXPEDITION_OPTIONS[str(minutes)]
    if p["food"]<50 or p["fuel"]<20:
        await call.answer("Для экспедиции нужно 50 еды и 20 топлива",show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE players SET food=food-50,fuel=fuel-20 WHERE user_id=?",(call.from_user.id,))
        await db.execute("""INSERT INTO expeditions(user_id,started_at,duration,reward_wood,reward_food,reward_metal,reward_fuel)
        VALUES(?,?,?,?,?,?,?)""",(call.from_user.id,time.time(),duration,wood,food,metal,fuel))
        await db.commit()
    await call.answer("Отряд отправлен!")
    await expedition_screen(call)

@dp.callback_query(F.data.startswith("e:claim:"))
async def cb_expedition_claim(call: CallbackQuery):
    exp_id=int(call.data.split(":")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        cur=await db.execute("SELECT * FROM expeditions WHERE id=? AND user_id=? AND status='active'",(exp_id,call.from_user.id))
        exp=await cur.fetchone()
        if not exp:
            await call.answer("Эта награда уже получена",show_alert=True); return
        if time.time()<exp["started_at"]+exp["duration"]:
            await call.answer("Отряд ещё в пути",show_alert=True); return
        await db.execute("UPDATE expeditions SET status='claimed' WHERE id=? AND status='active'",(exp_id,))
        await db.commit()
    await grant_resources(call.from_user.id,{"wood":exp["reward_wood"],"food":exp["reward_food"],"metal":exp["reward_metal"],"fuel":exp["reward_fuel"]})
    await add_xp(call.from_user.id,40)
    await quest_progress(call.from_user.id,"expedition","expedition",1)
    await call.answer("Награда получена!")
    await expedition_screen(call)

@dp.callback_query(F.data == "heroes")
async def cb_heroes(call: CallbackQuery):
    await heroes_screen(call)

@dp.callback_query(F.data == "defense")
async def cb_defense(call: CallbackQuery):
    p=await get_player(call.from_user.id); text,kb=defense_screen(p)
    await call.message.edit_text(text,parse_mode="HTML",reply_markup=kb); await call.answer()

@dp.callback_query(F.data.startswith("d:"))
async def cb_defense_upgrade(call: CallbackQuery):
    kind=call.data.split(":")[1]
    if kind not in ("wall","tower"):
        await call.answer("Ошибка",show_alert=True); return
    p=await refresh_player(await get_player(call.from_user.id))
    col="wall_level" if kind=="wall" else "tower_level"
    cost={"wood":300*p[col],"metal":150*p[col]}
    if kind=="tower": cost["fuel"]=50*p[col]
    if any(p[r]<n for r,n in cost.items()):
        await call.answer("Не хватает ресурсов: "+cost_text(cost),show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE players SET wood=wood-?,metal=metal-?,fuel=fuel-?,{col}={col}+1 WHERE user_id=?",
                         (cost.get("wood",0),cost.get("metal",0),cost.get("fuel",0),call.from_user.id)); await db.commit()
    await add_xp(call.from_user.id,40)
    p=await get_player(call.from_user.id); text,kb=defense_screen(p)
    await call.message.edit_text(f"✅ Укрепление улучшено. Получено 40 XP.\n\n{text}",parse_mode="HTML",reply_markup=kb)
    await call.answer("Оборона усилена!")

@dp.callback_query(F.data == "daily")
async def cb_daily(call: CallbackQuery):
    p=await get_player(call.from_user.id); today=day_key()
    if p["last_daily"]==today:
        await call.answer("Сегодня награда уже получена",show_alert=True); return
    yesterday=time.strftime("%Y-%m-%d",time.gmtime(time.time()-86400))
    streak=p["daily_streak"]+1 if p["last_daily"]==yesterday else 1
    reward_wood=150+streak*25; reward_food=100+streak*20; reward_crystals=10+min(streak,10)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE players SET last_daily=?,daily_streak=?,crystals=crystals+? WHERE user_id=?",
                         (today,streak,reward_crystals,call.from_user.id)); await db.commit()
    await grant_resources(call.from_user.id,{"wood":reward_wood,"food":reward_food})
    await add_xp(call.from_user.id,50)
    await call.message.edit_text(f"🎁 <b>Ежедневная награда получена!</b>\n\nСерия входов: {streak} дней\n🪵 {reward_wood}\n🍖 {reward_food}\n💎 {reward_crystals}\n⭐ 50 XP",parse_mode="HTML",reply_markup=main_keyboard())
    await call.answer("Награда получена!")

@dp.callback_query(F.data == "shop")
async def cb_shop(call: CallbackQuery):
    p=await get_player(call.from_user.id); text,kb=shop_screen(p)
    await call.message.edit_text(text,parse_mode="HTML",reply_markup=kb); await call.answer()

@dp.callback_query(F.data == "shop:starter")
async def cb_shop_starter(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        cur=await db.execute("SELECT COUNT(*) FROM daily_quests WHERE user_id=? AND target='starter_claimed'",(call.from_user.id,))
        # Отдельный флаг в таблице покупок не нужен: используем отдельную таблицу ниже.
        await db.execute("""CREATE TABLE IF NOT EXISTS claimed_bonuses(user_id INTEGER NOT NULL, code TEXT NOT NULL, PRIMARY KEY(user_id,code))""")
        try:
            await db.execute("INSERT INTO claimed_bonuses(user_id,code) VALUES(?, 'starter')",(call.from_user.id,))
        except aiosqlite.IntegrityError:
            await call.answer("Стартовый набор уже получен",show_alert=True); return
        await db.execute("UPDATE players SET crystals=crystals+100 WHERE user_id=?",(call.from_user.id,))
        await db.commit()
    p=await get_player(call.from_user.id); text,kb=shop_screen(p)
    await call.message.edit_text("🎁 Получено 100 бесплатных кристаллов!\n\n"+text,parse_mode="HTML",reply_markup=kb)
    await call.answer("Кристаллы начислены!")

@dp.callback_query(F.data == "alliance")
async def cb_alliance(call: CallbackQuery):
    await call.message.edit_text("🤝 <b>Альянсы</b>\n\nСистема альянсов будет следующим крупным этапом: создание союза, вступление, роли, казна и совместные события.\n\nПока раздел находится в разработке.",parse_mode="HTML",reply_markup=main_keyboard())
    await call.answer()

@dp.callback_query(F.data == "rating")
async def cb_rating(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        cur=await db.execute("SELECT name,level,xp,furnace_level FROM players ORDER BY level DESC,xp DESC,furnace_level DESC LIMIT 10")
        rows=await cur.fetchall()
    text="🏆 <b>Рейтинг выживших</b>\n\n"
    for i,r in enumerate(rows,1):
        text+=f"{i}. {html.escape(r['name'])} — ур. {r['level']} · печь {r['furnace_level']}\n"
    await call.message.edit_text(text,parse_mode="HTML",reply_markup=main_keyboard()); await call.answer()

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
