import os
import json
import logging
import asyncio
from aiohttp import web
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Logging setup
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')

if not BOT_TOKEN:
    raise RuntimeError('⚠️ BOT_TOKEN not set in .env')

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- JSON DATABASE SETUP ---
SETTINGS_FILE = 'settings.json'

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_settings(data):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# Global settings dictionary
chat_settings = load_settings()

WELCOME_TEXT = (
    "╭━━━━━━━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "┃  🤖 <b>SAFE AUTO REQUEST BOT</b>\n"
    "┃━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "┃\n"
    "┃  📌 <b>What this bot does:</b>\n"
    "┃  • Auto-join groups/channels\n"
    "┃  • Custom Welcome & Left Messages\n"
    "┃\n"
    "┃  ⚡ <b>Customization Commands:</b>\n"
    "┃  <code>/setleft &lt;channel_id&gt; &lt;msg&gt;</code>\n"
    "┃  <code>/setwelcome &lt;channel_id&gt; &lt;msg&gt;</code>\n"
    "┃\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
)

async def get_welcome_kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ Add to Group', url=f'https://t.me/{bot_username}?startgroup=true')],
        [InlineKeyboardButton(text='➕ Add to Channel', url=f'https://t.me/{bot_username}?startchannel=start')]
    ])

@dp.message(CommandStart())
async def cmd_start(msg: types.Message):
    if msg.chat.type != "private": return
    me = await bot.get_me()
    kb = await get_welcome_kb(me.username)
    await msg.answer(WELCOME_TEXT, reply_markup=kb)

# 👇 COMMAND: SET LEFT MESSAGE
@dp.message(Command("setleft"))
async def cmd_setleft(msg: types.Message):
    if msg.chat.type != "private": return
    
    args = msg.text.split(maxsplit=2)
    if len(args) < 3:
        await msg.answer("❌ <b>Sahi format:</b>\n<code>/setleft -100123456789 Mera custom goodbye message</code>")
        return
    
    chat_id, custom_msg = args[1], args[2]
    
    try:
        member = await bot.get_chat_member(chat_id, msg.from_user.id)
        if member.status not in ['administrator', 'creator']:
            await msg.answer("❌ Aap is channel ke Admin nahi hain!")
            return
    except Exception:
        await msg.answer("❌ Pehle bot ko apne channel mein Admin banayein aur sahi ID dalein.")
        return

    if chat_id not in chat_settings:
        chat_settings[chat_id] = {}
    chat_settings[chat_id]['left_msg'] = custom_msg
    save_settings(chat_settings)
    
    await msg.answer(f"✅ <b>Done!</b> Is channel ke liye Left message set ho gaya hai:\n\n{custom_msg}")

# 👇 COMMAND: SET WELCOME MESSAGE
@dp.message(Command("setwelcome"))
async def cmd_setwelcome(msg: types.Message):
    if msg.chat.type != "private": return
    
    args = msg.text.split(maxsplit=2)
    if len(args) < 3:
        await msg.answer("❌ <b>Sahi format:</b>\n<code>/setwelcome -100123456789 Mera custom welcome message</code>")
        return
    
    chat_id, custom_msg = args[1], args[2]
    
    try:
        member = await bot.get_chat_member(chat_id, msg.from_user.id)
        if member.status not in ['administrator', 'creator']:
            await msg.answer("❌ Aap is channel ke Admin nahi hain!")
            return
    except Exception:
        await msg.answer("❌ Pehle bot ko apne channel mein Admin banayein aur sahi ID dalein.")
        return

    if chat_id not in chat_settings:
        chat_settings[chat_id] = {}
    chat_settings[chat_id]['welcome_msg'] = custom_msg
    save_settings(chat_settings)
    
    await msg.answer(f"✅ <b>Done!</b> Is channel ke liye Welcome message set ho gaya hai:\n\n{custom_msg}")

# 👇 AUTO APPROVE & CUSTOM WELCOME
@dp.chat_join_request()
async def auto_approve_join_request(update: types.ChatJoinRequest):
    user_id = update.from_user.id
    chat_id = str(update.chat.id)
    
    # Agar is channel ke liye custom welcome msg set hai, toh pehle bhej do
    if chat_id in chat_settings and 'welcome_msg' in chat_settings[chat_id]:
        try:
            await bot.send_message(chat_id=user_id, text=chat_settings[chat_id]['welcome_msg'])
        except Exception:
            pass # User ne bot start nahi kiya hoga
    
    # Fir request approve karo
    try:
        await update.approve()
    except Exception as e:
        logging.error(f"Failed to approve user: {e}")

# 👇 CUSTOM LEFT MESSAGE HANDLER
@dp.chat_member()
async def on_chat_member_update(update: types.ChatMemberUpdated):
    user = update.from_user
    chat_id = str(update.chat.id)

    if update.old_chat_member.status in ['member', 'administrator'] and update.new_chat_member.status in ['left', 'kicked']:
        # Agar admin ne custom set kiya hai toh wo use karo, warna default use karo
        default_left = (
            "🌟 ALL DRAMA DIRECT FILES AVAILABLE 🗃️\n\n"
            "https://t.me/+amS1Q3R4_Qg5NjU1\n"
            "https://t.me/+amS1Q3R4_Qg5NjU1"
        )
        
        final_msg = chat_settings.get(chat_id, {}).get('left_msg', default_left)
        
        try:
            await bot.send_message(chat_id=user.id, text=final_msg)
        except Exception:
            pass # Blocked or strict privacy

# 👇 DUMMY WEB SERVER (Render ke liye)
async def handle_ping(request):
    return web.Response(text="Bot is running beautifully! 🚀")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def main():
    await start_dummy_server()
    logging.info("🤖 Safe Auto-Approve Bot is running...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
