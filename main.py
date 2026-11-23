import asyncio
import html
import logging
import os
from urllib.parse import quote

import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, CommandObject
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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)  # bot egasining Telegram ID

# Majburiy kanal Render Environment'dan olinadi:
#   CHANNEL_ID   -> kanal ID (-100...) yoki @username
#   CHANNEL_LINK -> tugma ochadigan havola
#   CHANNEL_NAME -> tugma yozuvi (ixtiyoriy)
# Bot kanalda ADMIN bo'lishi shart
_cid = os.getenv("CHANNEL_ID", "").strip()
CHANNEL_ID = int(_cid) if _cid.lstrip("-").isdigit() else _cid
CHANNELS = [
    (
        os.getenv("CHANNEL_NAME", "📢 Shaxsiy kanal"),
        CHANNEL_ID,
        os.getenv("CHANNEL_LINK", "").strip(),
    ),
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

# ---------- Premium emoji yordamchilari ----------
# Render Environment'da EM_<NOM> = premium emoji ID berilsa, premium emoji
# ishlatiladi. Berilmasa oddiy emoji ko'rinadi (xato bermaydi).
# Premium emoji faqat bot egasida Telegram Premium bo'lsa ishlaydi.
def E(fallback: str, name: str) -> str:
    cid = os.getenv(f"EM_{name}", "").strip()
    if cid:
        return f'<tg-emoji emoji-id="{cid}">{fallback}</tg-emoji>'
    return fallback


def ibtn(label, fallback, name=None, style=None, **kw) -> InlineKeyboardButton:
    """Tugma: premium ikonka (bo'lsa) + rang (primary/success/danger)."""
    extra = {}
    cid = os.getenv(f"EM_{name}", "").strip() if name else ""
    if cid:
        extra["icon_custom_emoji_id"] = cid
        text = label
    else:
        text = f"{fallback} {label}".strip()
    if style:
        extra["style"] = style
    return InlineKeyboardButton(text=text, **kw, **extra)


EFFECT_PARTY = "5046509860389126442"  # 🎉 xabar effekti

CHANNELS_TEXT = (
    f"{E('📢', 'MEGAPHONE')} <b>Majburiy kanallar</b>\n\n"
    f"{E('🎁', 'GIFT')} Sovg'alarga ega bo'lish uchun quyidagi barcha kanallarga "
    "a'zo bo'lishingiz shart.\n\n"
    "<blockquote>Kanallarga a'zo bo'lganingizdan so'ng, "
    f"{E('✅', 'CHECK')} <b>Obuna bo'ldim</b> tugmasini bosing.</blockquote>"
)

GIFTS_TEXT = (
    f"{E('🎁', 'GIFT')} <b>Sovg'alar</b>\n\n"
    "<blockquote>"
    "🥇 1-o'rin — ...\n"
    "🥈 2-o'rin — ...\n"
    "🥉 3-o'rin — ..."
    "</blockquote>"
)

RULES_TEXT = (
    f"{E('💡', 'BULB')} <b>Konkurs shartlari</b>\n\n"
    "<blockquote>"
    "1. Barcha majburiy kanallarga a'zo bo'ling.\n"
    "2. Shaxsiy havolangizni do'stlaringizga yuboring.\n"
    "3. Do'stingiz havola orqali kirib, kanallarga a'zo bo'lsa — sizga +1 ball.\n"
    "4. Eng ko'p ball to'plaganlar g'olib bo'ladi."
    "</blockquote>"
)

SHARE_TEXT = (
    "📚 Kitobxon konkursida qatnashing! "
    "Do'stlaringizni yig'ing va eng zo'r kitoblarga ega bo'ling!"
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
        [InlineKeyboardButton(text=name, url=link, style="primary")]
        for name, _, link in CHANNELS
    ]
    rows.append(
        [ibtn("Obuna bo'ldim", "✅", "CHECK", "success", callback_data="check_sub")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def share_kb(link: str) -> InlineKeyboardMarkup:
    share = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(SHARE_TEXT)}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("Referal havolani ulashish", "🔗", "LINK", "primary", url=share)],
            [ibtn("Referallarimni ko'rish", "👥", "USERS", callback_data="my_refs")],
        ]
    )


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
    await db.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
    )
    await db.commit()


async def get_setting(key: str, default: str) -> str:
    cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    return row[0] if row else default


async def set_setting(key: str, value: str):
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
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


async def refs_list(user_id: int, limit: int = 20):
    cur = await db.execute(
        "SELECT full_name FROM users WHERE referrer_id=? AND verified=1 "
        "ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )
    return await cur.fetchall()


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

    user = await get_user(u.id)
    if user["verified"] and await is_subscribed(bot, u.id):
        await message.answer(f"{E('✅', 'CHECK')} <b>Asosiy menyu</b>", reply_markup=main_menu)
    else:
        await message.answer(CHANNELS_TEXT, reply_markup=channels_kb())


@router.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery, bot: Bot):
    if not await is_subscribed(bot, call.from_user.id):
        await call.answer("❌ Hali barcha kanallarga a'zo bo'lmadingiz!", show_alert=True)
        return

    first_time = await mark_verified(call.from_user.id)
    await call.message.delete()
    sticker = os.getenv("WELCOME_STICKER", "").strip()
    if sticker:
        try:
            await call.message.answer_sticker(sticker)
        except Exception as e:
            logging.warning("Stiker yuborilmadi: %s", e)
    text = f"{E('✅', 'CHECK')} <b>Obuna tasdiqlandi!</b>\n\n<blockquote>Endi referal havolangizni oling va do'stlaringizni taklif qiling.</blockquote>"
    try:
        await call.message.answer(text, reply_markup=main_menu, message_effect_id=EFFECT_PARTY)
    except Exception:
        await call.message.answer(text, reply_markup=main_menu)

    if first_time:
        user = await get_user(call.from_user.id)
        if user and user["referrer_id"]:
            try:
                total = await count_refs(user["referrer_id"])
                await bot.send_message(
                    user["referrer_id"],
                    f"{E('🎉', 'PARTY')} <b>Sizning havolangiz orqali yangi do'st qo'shildi!</b>\n"
                    f"{E('👥', 'USERS')} Jami takliflaringiz: <b>{total}</b>",
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


# ---------- ADMIN: matnlarni bot ichidan o'zgartirish ----------
async def _save_text(message: Message, key: str, title: str):
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        return
    src = message.reply_to_message
    if not src or not src.text:
        await message.answer(
            f"{title} matnini yangi xabar qilib yuboring, so'ng shu xabarga "
            f"<b>javob (reply)</b> qilib {message.text.split()[0]} deb yozing."
        )
        return
    await set_setting(key, src.html_text)
    await message.answer(f"✅ {title} yangilandi. Ko'rish uchun menyudan tekshiring.")


@router.message(Command("setrules"))
async def set_rules(message: Message):
    await _save_text(message, "rules", "Shartlar")


@router.message(Command("setgifts"))
async def set_gifts(message: Message):
    await _save_text(message, "gifts", "Sovg'alar")


@router.message(Command("resettexts"))
async def reset_texts(message: Message):
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        return
    await db.execute("DELETE FROM settings WHERE key IN ('rules','gifts')")
    await db.commit()
    await message.answer("♻️ Shartlar va sovg'alar matni kodga yozilgan holatga qaytdi.")


@router.message(F.forward_origin)
async def get_channel_id(message: Message):
    """Kanaldan post forward qilinsa, kanal ID'sini qaytaradi."""
    chat = getattr(message.forward_origin, "chat", None)
    if chat:
        await message.answer(f"Kanal ID: <code>{chat.id}</code>")


@router.message(F.entities.func(lambda e: bool(e) and any(x.type == "custom_emoji" for x in e)))
async def get_emoji_ids(message: Message):
    """Premium emoji yuborilsa, uning ID'sini qaytaradi."""
    ids = [x.custom_emoji_id for x in message.entities if x.type == "custom_emoji"]
    await message.answer("Premium emoji ID:\n" + "\n".join(f"<code>{i}</code>" for i in ids))


@router.message(F.sticker)
async def get_sticker_id(message: Message):
    """Stiker yuborilsa, uning file_id'sini qaytaradi."""
    st = message.sticker
    text = f"Stiker file_id:\n<code>{st.file_id}</code>"
    if st.custom_emoji_id:
        text += f"\n\nEmoji ID:\n<code>{st.custom_emoji_id}</code>"
    await message.answer(text)


@router.message(F.text == BTN_LINK)
async def my_link(message: Message, bot: Bot):
    if not await guard(message, bot):
        return
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
    await message.answer(
        f"{E('🔗', 'LINK')} <b>Sizning shaxsiy referal havolangiz</b>\n\n"
        f"{link}\n\n"
        "<blockquote>Havolani do'stlaringiz bilan ulashing. Referal ular barcha "
        "majburiy kanallarga a'zo bo'lgandan keyingina hisoblanadi.</blockquote>",
        reply_markup=share_kb(link),
    )


@router.callback_query(F.data == "my_refs")
async def my_refs(call: CallbackQuery, bot: Bot):
    if not await is_subscribed(bot, call.from_user.id):
        await call.answer("❌ Avval kanalga a'zo bo'ling!", show_alert=True)
        return
    uid = call.from_user.id
    total = await count_refs(uid)
    rows = await refs_list(uid, 20)
    if not rows:
        body = "Hozircha hech kim qo'shilmagan.\nHavolangizni do'stlaringizga yuboring!"
    else:
        body = "\n".join(
            f"{i}. {html.escape(r['full_name'] or 'Foydalanuvchi')}"
            for i, r in enumerate(rows, 1)
        )
        if total > len(rows):
            body += f"\n... va yana {total - len(rows)} ta"
    await call.message.answer(
        f"{E('👥', 'USERS')} <b>Mening referallarim: {total} ta</b>\n\n"
        f"<blockquote expandable>{body}</blockquote>"
    )
    await call.answer()


@router.message(F.text == BTN_ACCOUNT)
async def my_account(message: Message, bot: Bot):
    if not await guard(message, bot):
        return
    uid = message.from_user.id
    cnt = await count_refs(uid)
    place = await my_place(uid)
    await message.answer(
        f"{E('📄', 'DOC')} <b>Mening hisobim</b>\n\n"
        "<blockquote>"
        f"👤 {html.escape(message.from_user.full_name)}\n"
        f"{E('👥', 'USERS')} Taklif qilganlar: <b>{cnt}</b>\n"
        f"{E('⭐️', 'STAR')} Ballar: <b>{cnt}</b>\n"
        f"{E('🏆', 'TROPHY')} O'rningiz: <b>{place if place else '—'}</b>"
        "</blockquote>"
    )


@router.message(F.text == BTN_GIFTS)
async def gifts(message: Message, bot: Bot):
    if await guard(message, bot):
        await message.answer(await get_setting("gifts", GIFTS_TEXT))


@router.message(F.text == BTN_RULES)
async def rules(message: Message, bot: Bot):
    if await guard(message, bot):
        await message.answer(await get_setting("rules", RULES_TEXT))


@router.message(F.text == BTN_RATING)
async def rating(message: Message, bot: Bot):
    if not await guard(message, bot):
        return
    rows = await top_users(10)
    if not rows:
        await message.answer("📊 Reyting hozircha bo'sh.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, r in enumerate(rows):
        mark = medals[i] if i < 3 else f"{i + 1}."
        name = html.escape(r["full_name"] or "Foydalanuvchi")
        lines.append(f"{mark} {name} — <b>{r['cnt']}</b>")
    await message.answer(
        f"{E('📊', 'CHART')} <b>TOP-10 reyting</b>\n\n"
        "<blockquote>" + "\n".join(lines) + "</blockquote>"
    )


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
