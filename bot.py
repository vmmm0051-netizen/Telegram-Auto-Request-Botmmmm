import os
import logging
import asyncio
import time
from aiohttp import web
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

logging.basicConfig(level=logging.INFO)
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')

if not BOT_TOKEN or not MONGO_URI:
    raise RuntimeError('⚠️ BOT_TOKEN or MONGO_URI not set!')

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- MONGODB SETUP ---
client = AsyncIOMotorClient(MONGO_URI)
db = client.bot_database
settings_col = db.chat_settings

# --- FSM STATES ---
class MsgSetup(StatesGroup):
    waiting_for_forward = State()
    waiting_for_text = State()

WELCOME_TEXT = "🤖 <b>SAFE AUTO REQUEST BOT IS ONLINE</b>"

@dp.message(CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    if msg.chat.type != "private": return
    await msg.answer(WELCOME_TEXT)

# --- DATABASE HELPER FUNCTIONS ---
async def get_chat_data(chat_id: str):
    data = await settings_col.find_one({"chat_id": chat_id})
    return data if data else {"chat_id": chat_id, "filters": {}, "cleanup": []}

async def update_chat_data(chat_id: str, update_dict: dict):
    await settings_col.update_one({"chat_id": chat_id}, {"$set": update_dict}, upsert=True)

# --- GROUP FILTERS ---
async def is_admin(msg: types.Message) -> bool:
    if msg.sender_chat and str(msg.sender_chat.id) == str(msg.chat.id): return True
    try:
        member = await bot.get_chat_member(msg.chat.id, msg.from_user.id)
        return member.status in ['administrator', 'creator']
    except: return False

@dp.message(Command("addfilter"))
async def cmd_addfilter(msg: types.Message):
    if msg.chat.type in ['private', 'channel'] or not await is_admin(msg): return
    args = msg.text.split(maxsplit=2)
    if len(args) < 3: return await msg.reply("❌ Format: /addfilter keyword reply")
    
    chat_data = await get_chat_data(str(msg.chat.id))
    chat_data['filters'][args[1].lower()] = args[2]
    await update_chat_data(str(msg.chat.id), chat_data)
    await msg.reply(f"✅ Filter <b>{args[1]}</b> added!")

@dp.message(Command("delfilter"))
async def cmd_delfilter(msg: types.Message):
    if msg.chat.type in ['private', 'channel'] or not await is_admin(msg): return
    args = msg.text.split(maxsplit=1)
    if len(args) < 2: return
    
    chat_data = await get_chat_data(str(msg.chat.id))
    if args[1].lower() in chat_data['filters']:
        del chat_data['filters'][args[1].lower()]
        await update_chat_data(str(msg.chat.id), chat_data)
        await msg.reply("🗑️ Filter deleted.")

@dp.message(F.text)
async def filter_handler(msg: types.Message):
    if msg.chat.type == 'private' or msg.text.startswith('/'): return
    chat_data = await get_chat_data(str(msg.chat.id))
    
    for kw, reply in chat_data['filters'].items():
        if kw in msg.text.lower():
            sent = await msg.reply(reply, link_preview_options=types.LinkPreviewOptions(is_disabled=True))
            chat_data['cleanup'].append({
                'chat_id': sent.chat.id, 'message_id': sent.message_id,
                'delete_at': time.time() + 3600
            })
            await update_chat_data(str(msg.chat.id), chat_data)
            break

# --- BACKGROUND CLEANUP ---
async def cleanup_background_task():
    while True:
        await asyncio.sleep(60)
        async for chat in settings_col.find({"cleanup": {"$not": {"$size": 0}}}):
            current_time = time.time()
            valid_msgs = []
            for item in chat['cleanup']:
                if current_time >= item['delete_at']:
                    try: await bot.delete_message(chat_id=item['chat_id'], message_id=item['message_id'])
                    except: pass
                else: valid_msgs.append(item)
            await update_chat_data(chat['chat_id'], {"cleanup": valid_msgs})

async def main():
    asyncio.create_task(cleanup_background_task())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
