import asyncio
import logging
import os

import asyncpg
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
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL (Neon/Supabase/Render)
PORT = int(os.getenv("PORT", "10000"))

# Majburiy kanallar: (nomi, @username)  -- bot har bir kanalda ADMIN bo'lishi shart
CHANNELS = [
    ("📢 Abdurahmon Sharipov | Shunchaki sahifa", "@kanal1_username"),
    ("📢 Multilevel Marathon", "@kanal2_username"),
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
        [InlineKeyboardButton(text=name, url=f"https://t.me/{user.lstrip('@')}")]
        for name, user in CHANNELS
    ]
    rows.append(
        [InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ================== BAZA ==================
pool: asyncpg.Pool = None


async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as c:
        await c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id     BIGINT PRIMARY KEY,
                full_name   TEXT,
                username    TEXT,
                referrer_id BIGINT,
                verified    BOOLEAN DEFAULT FALSE,
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )


async def add_user(user_id: int, full_name: str, username: str, referrer_id):
    async with pool.acquire() as c:
        # faqat yangi foydalanuvchiga referrer yoziladi, o'zini o'zi taklif qila olmaydi
        if referrer_id == user_id:
            referrer_id = None
        await c.execute(
            """
            INSERT INTO users (user_id, full_name, username, referrer_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id, full_name, username, referrer_id,
        )


async def get_user(user_id: int):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)


async def mark_verified(user_id: int) -> bool:
    """True qaytadi, agar birinchi marta tasdiqlangan bo'lsa."""
    async with pool.acquire() as c:
        res = await c.execute(
            "UPDATE users SET verified=TRUE WHERE user_id=$1 AND verified=FALSE",
            user_id,
        )
        return res.endswith("1")


async def count_refs(user_id: int) -> int:
    async with pool.acquire() as c:
        return await c.fetchval(
            "SELECT COUNT(*) FROM users WHERE referrer_id=$1 AND verified=TRUE",
            user_id,
        )


async def top_users(limit: int = 10):
    async with pool.acquire() as c:
        return await c.fetch(
            """
            SELECT u.user_id, u.full_name, COUNT(r.user_id) AS cnt
            FROM users u
            JOIN users r ON r.referrer_id = u.user_id AND r.verified = TRUE
            GROUP BY u.user_id, u.full_name
            ORDER BY cnt DESC
            LIMIT $1
            """,
            limit,
        )


async def my_place(user_id: int):
    async with pool.acquire() as c:
        return await c.fetchval(
            """
            SELECT place FROM (
                SELECT referrer_id, RANK() OVER (ORDER BY COUNT(*) DESC) AS place
                FROM users
                WHERE referrer_id IS NOT NULL AND verified = TRUE
                GROUP BY referrer_id
            ) t WHERE referrer_id = $1
            """,
            user_id,
        )


# ================== OBUNA TEKSHIRISH ==================
async def is_subscribed(bot: Bot, user_id: int) -> bool:
    for _, channel in CHANNELS:
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
