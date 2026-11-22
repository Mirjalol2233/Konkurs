import asyncio
import logging
import os

import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# ================== SOZLAMALAR (Render Environment'da beriladi) ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "bot.db")
PORT = int(os.getenv("PORT", "10000"))

# Majburiy kanallar: (tugma nomi, kanal ID yoki @username, havola)
# Ochiq kanal:  ("Nomi", "@username", "https://t.me/username")
# Yopiq kanal:  ("Nomi", -1001234567890, "https://t.me/+AbCdEf...")
# Bot har bir kanalda ADMIN bo'lishi shart
CHANNELS = [
    ("📢 Abdurahmon Sharipov | Shunchaki sahifa", "@kanal1_username", "https://t.me/kanal1_username"),
    ("📢 Multilevel Marathon", -1001234567890, "https://t.me/+yopiq_havola"),
]

ABOUT_TEXT = (
    "<b>Bu bot nimalar qila oladi?</b>\n\n"
    "📚 Assalomu alaykum, aziz kitobxon!\n\n"
    "Bu bot orqali eng ajoyib kitoblar dunyosiga sayohat qilasiz 🌍\n\n"
    "📖 Minglab kitoblar bir joyda\n"
    "🔗 Do'stingizga havola ulashing\n"
    "👥 Har bir do'st — sizga yangi imkoniyat\n"
    "🎁 Ball to'plab, kitoblar va yopiq kutubxonani oching\n\n"
    "Bilim — eng qimmatli sarmoya 💎\n"
    "«Start»ni bosing va boshlang!"
)

CHANNELS_TEXT = (
    "📢 <b>Majburiy kanallar</b>\n\n"
    "🎁 Sovg'alarga ega bo'lish uchun quyidagi barcha kanallarga "
    "a'zo bo'lishingiz shart.\n\n"
    "Kanallarga a'zo bo'lganingizdan so'ng, ✅ <b>Obunani tekshirish</b> "
    "tugmasini bosing."
)

GIFTS_TEXT = (
    "🎁 <b>Sovg'alar</b>\n\n"
    "🥇 1-o'rin — ...\n"
    "🥈 2-o'rin — ...\n"
    "🥉 3-o'rin — ...\n"
)

RULES_TEXT = (
    "💡 <b>Konkurs shartlari</b>\n\n"
    "1. Barcha majburiy kanallarga a'zo bo'ling.\n"
    "2. Shaxsiy havolangizni do'stlaringizga yuboring.\n"
    "3. Do'stingiz havola orqali kirib, kanallarga a'zo bo'lsa — sizga +1 ball.\n"
    "4. Eng ko'p ball to'plaganlar g'olib bo'ladi.\n"
)

# ================== TUGMALAR ==================
BTN_LINK = "🔗 Mening shaxsiy linkim"
BTN_GIFTS = "🎁 Sovg'alar"
BTN_ACCOUNT = "📄 Mening hisobim"
BTN_RULES = "💡 Shartlar"
BTN_RATING = "📊 Reyting"

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_LINK)],
        [KeyboardButton(text=BTN_GIFTS), KeyboardButton(text=BTN_ACCOUNT)],
        [KeyboardButton(text=BTN_RULES), KeyboardButton(text=BTN_RATING)],
    ],
    resize_keyboard=True,
)


def channels_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=name, url=link)]
        for name, _, link in CHANNELS
    ]
    rows.append(
        [InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ================== BAZA (SQLite) ==================
db: aiosqlite.Connection = None


async def init_db():
    global db
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            full_name   TEXT,
            username    TEXT,
            referrer_id INTEGER,
            verified    INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.commit()


async def add_user(user_id: int, full_name: str, username: str, referrer_id):
    if referrer_id == user_id:
        referrer_id = None
    await db.execute(
        "INSERT OR IGNORE INTO users (user_id, full_name, username, referrer_id) "
        "VALUES (?, ?, ?, ?)",
        (user_id, full_name, username, referrer_id),
    )
    await db.commit()


async def get_user(user_id: int):
    cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    return await cur.fetchone()


async def mark_verified(user_id: int) -> bool:
    """True qaytadi, agar birinchi marta tasdiqlangan bo'lsa."""
    cur = await db.execute(
        "UPDATE users SET verified=1 WHERE user_id=? AND verified=0", (user_id,)
    )
    await db.commit()
    return cur.rowcount == 1


async def count_refs(user_id: int) -> int:
    cur = await db.execute(
        "SELECT COUNT(*) FROM users WHERE referrer_id=? AND verified=1", (user_id,)
    )
    return (await cur.fetchone())[0]


async def top_users(limit: int = 10):
    cur = await db.execute(
        """
        SELECT u.user_id, u.full_name, COUNT(r.user_id) AS cnt
        FROM users u
        JOIN users r ON r.referrer_id = u.user_id AND r.verified = 1
        GROUP BY u.user_id, u.full_name
        ORDER BY cnt DESC
        LIMIT ?
        """,
        (limit,),
    )
    return await cur.fetchall()


async def my_place(user_id: int):
    cur = await db.execute(
        """
        SELECT place FROM (
            SELECT referrer_id, RANK() OVER (ORDER BY COUNT(*) DESC) AS place
            FROM users
            WHERE referrer_id IS NOT NULL AND verified = 1
            GROUP BY referrer_id
        ) t WHERE referrer_id = ?
        """,
        (user_id,),
    )
    row = await cur.fetchone()
    return row[0] if row else None


# ================== OBUNA TEKSHIRISH ==================
async def is_subscribed(bot: Bot, user_id: int) -> bool:
    for _, channel, _ in CHANNELS:
        try:
            m = await bot.get_chat_member(channel, user_id)
            if m.status in ("left", "kicked"):
                return False
        except Exception as e:
            logging.warning("Kanal tekshirishda xato (%s): %s", channel, e)
            return False
    return True


# ================== HANDLERLAR ==================
router = Router()


@router.message(CommandStart())
async def start(message: Message, command: CommandObject, bot: Bot):
    ref = None
    if command.args and command.args.startswith("ref_"):
        try:
            ref = int(command.args[4:])
        except ValueError:
            ref = None

    u = message.from_user
    await add_user(u.id, u.full_name, u.username, ref)

    await message.answer(ABOUT_TEXT)
    user = await get_user(u.id)
    if user["verified"] and await is_subscribed(bot, u.id):
        await message.answer("✅ Asosiy menyu:", reply_markup=main_menu)
    else:
        await message.answer(CHANNELS_TEXT, reply_markup=channels_kb())


@router.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery, bot: Bot):
    if not await is_subscribed(bot, call.from_user.id):
        await call.answer("❌ Hali barcha kanallarga a'zo bo'lmadingiz!", show_alert=True)
        return

    first_time = await mark_verified(call.from_user.id)
    await call.message.delete()
    await call.message.answer("✅ Obuna tasdiqlandi!", reply_markup=main_menu)

    if first_time:
        user = await get_user(call.from_user.id)
        if user and user["referrer_id"]:
            try:
                total = await count_refs(user["referrer_id"])
                await bot.send_message(
                    user["referrer_id"],
                    f"🎉 Sizning havolangiz orqali yangi do'st qo'shildi!\n"
                    f"👥 Jami takliflaringiz: <b>{total}</b>",
                )
            except Exception:
                pass
    await call.answer()


async def guard(message: Message, bot: Bot) -> bool:
    """Menyu tugmalari faqat obuna bo'lganlarga ishlaydi."""
    if not await is_subscribed(bot, message.from_user.id):
        await message.answer(CHANNELS_TEXT, reply_markup=channels_kb())
        return False
    return True


@router.message(F.text == BTN_LINK)
async def my_link(message: Message, bot: Bot):
    if not await guard(message, bot):
        return
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
    await message.answer(
        "🔗 <b>Sizning shaxsiy havolangiz:</b>\n\n"
        f"{link}\n\n"
        "Do'stlaringizga yuboring. Har bir a'zo bo'lgan do'st uchun +1 ball!"
    )


@router.message(F.text == BTN_ACCOUNT)
async def my_account(message: Message, bot: Bot):
    if not await guard(message, bot):
        return
    uid = message.from_user.id
    cnt = await count_refs(uid)
    place = await my_place(uid)
    await message.answer(
        "📄 <b>Mening hisobim</b>\n\n"
        f"👤 {message.from_user.full_name}\n"
        f"👥 Taklif qilganlar: <b>{cnt}</b>\n"
        f"⭐️ Ballar: <b>{cnt}</b>\n"
        f"🏆 O'rningiz: <b>{place if place else '—'}</b>"
    )


@router.message(F.text == BTN_GIFTS)
async def gifts(message: Message, bot: Bot):
    if await guard(message, bot):
        await message.answer(GIFTS_TEXT)


@router.message(F.text == BTN_RULES)
async def rules(message: Message, bot: Bot):
    if await guard(message, bot):
        await message.answer(RULES_TEXT)


@router.message(F.text == BTN_RATING)
async def rating(message: Message, bot: Bot):
    if not await guard(message, bot):
        return
    rows = await top_users(10)
    if not rows:
        await message.answer("📊 Reyting hozircha bo'sh.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["📊 <b>TOP-10 reyting</b>\n"]
    for i, r in enumerate(rows):
        mark = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{mark} {r['full_name']} — <b>{r['cnt']}</b>")
    await message.answer("\n".join(lines))


# ================== RENDER UCHUN WEB SERVER ==================
async def health(_):
    return web.Response(text="OK")


async def run_web():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()


async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await run_web()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
