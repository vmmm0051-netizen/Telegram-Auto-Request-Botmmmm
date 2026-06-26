import os
import json
import logging
import asyncio
import time
from aiohttp import web
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Logging setup
logging.basicConfig(level=logging.INFO)

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

chat_settings = load_settings()

# --- FSM STATES ---
class MsgSetup(StatesGroup):
    waiting_for_forward = State()
    waiting_for_text = State()

WELCOME_TEXT = (
    "╭━━━━━━━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "┃  🤖 <b>SAFE AUTO REQUEST BOT</b>\n"
    "┃━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "┃\n"
    "┃  📌 <b>Commands:</b>\n"
    "┃  /help - Learn how to use this bot\n"
    "┃  /setwelcome - Set welcome message\n"
    "┃  /setleft - Set goodbye message\n"
    "┃  /offwelcome - Turn OFF welcome\n"
    "┃  /offleft - Turn OFF goodbye\n"
    "┃  /cancel - Cancel process\n"
    "┃\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
)

async def get_welcome_kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ Add to Group', url=f'https://t.me/{bot_username}?startgroup=true')],
        [InlineKeyboardButton(text='➕ Add to Channel', url=f'https://t.me/{bot_username}?startchannel=start')]
    ])

@dp.message(CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    if msg.chat.type != "private": return
    await state.clear()
    me = await bot.get_me()
    kb = await get_welcome_kb(me.username)
    await msg.answer(WELCOME_TEXT, reply_markup=kb)

@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    if msg.chat.type != "private": return
    help_text = (
        "📖 <b>How to Use This Bot:</b>\n\n"
        "<b>1. Channel DMs (Welcome/Left):</b>\n"
        "• Send <code>/setwelcome</code> or <code>/setleft</code> in my DM.\n"
        "• Forward a message from your channel.\n"
        "• Type your custom text.\n\n"
        "<b>2. Group Filters (Bot replies auto-delete after 1 hr):</b>\n"
        "• <code>/addfilter &lt;word&gt; &lt;reply_text&gt;</code> - Add permanent filter\n"
        "• <code>/delfilter &lt;word&gt;</code> - Delete a single filter\n"
        "• <code>/delallfilters</code> - Delete all filters\n"
        "• <code>/filters</code> - View active filters\n\n"
        "<i>Note: You must be an Admin to use these commands.</i>"
    )
    await msg.answer(help_text)

# --- CHANNEL DM SETUP (WELCOME/LEFT) ---
@dp.message(Command("setleft", "setwelcome", "offleft", "offwelcome"))
async def start_setting_msg(msg: types.Message, state: FSMContext):
    if msg.chat.type != "private": return
    cmd = msg.text.split()[0]
    msg_type = "left_msg" if cmd in ["/setleft", "/offleft"] else "welcome_msg"
    action = "off" if cmd.startswith("/off") else "set"
    await state.update_data(msg_type=msg_type, action=action)
    await state.set_state(MsgSetup.waiting_for_forward)
    await msg.answer("📢 Please <b>Forward</b> any message from your Channel here.")

@dp.message(MsgSetup.waiting_for_forward)
async def process_forwarded_msg(msg: types.Message, state: FSMContext):
    if not msg.forward_origin or msg.forward_origin.type != 'channel':
        await msg.answer("❌ This is not a forwarded message from a channel.")
        return
    channel_id = str(msg.forward_origin.chat.id)
    channel_title = msg.forward_origin.chat.title
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=msg.from_user.id)
        if member.status not in ['administrator', 'creator']:
            await msg.answer("❌ You are not an Admin of this channel!")
            return
    except Exception:
        await msg.answer("❌ Please make the bot an Admin in your channel first.")
        return

    data = await state.get_data()
    msg_type, action = data['msg_type'], data['action']
    if channel_id not in chat_settings: chat_settings[channel_id] = {}

    if action == "off":
        chat_settings[channel_id][msg_type] = "OFF"
        save_settings(chat_settings)
        await msg.answer(f"✅ The message for '{channel_title}' has been turned <b>OFF</b>.")
        await state.clear()
        return

    await state.update_data(channel_id=channel_id, channel_title=channel_title)
    await state.set_state(MsgSetup.waiting_for_text)
    await msg.answer(f"✅ Channel verified: <b>{channel_title}</b>\n\n📝 Now, type and send your new custom Message.")

@dp.message(MsgSetup.waiting_for_text)
async def process_custom_msg(msg: types.Message, state: FSMContext):
    if not msg.text: 
        await msg.answer("❌ Please send text only.")
        return
    data = await state.get_data()
    chat_settings[data['channel_id']][data['msg_type']] = msg.html_text 
    save_settings(chat_settings)
    await msg.answer(f"✅ Message successfully set:\n\n{msg.html_text}")
    await state.clear()

@dp.message(Command("cancel"))
async def cmd_cancel(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("❌ Process cancelled.")

# 👇 SMART ADMIN CHECK (Personal + Anonymous Support)
async def is_admin(msg: types.Message) -> bool:
    # Agar chat as a Group (Anonymous) aayi hai
    if msg.sender_chat and str(msg.sender_chat.id) == str(msg.chat.id):
        return True
    # Agar personal ID se aayi hai
    try:
        member = await bot.get_chat_member(msg.chat.id, msg.from_user.id)
        return member.status in ['administrator', 'creator']
    except Exception:
        return False

# --- GROUP FILTER COMMANDS ---
@dp.message(Command("addfilter"))
async def cmd_addfilter(msg: types.Message):
    if msg.chat.type in ['private', 'channel']: return
    if not await is_admin(msg): return

    args = msg.text.split(maxsplit=2)
    if len(args) < 3:
        await msg.reply("❌ Format: <code>/addfilter keyword your reply message</code>")
        return

    keyword = args[1].lower()
    reply_text = args[2]
    chat_id = str(msg.chat.id)

    if chat_id not in chat_settings: chat_settings[chat_id] = {}
    if 'filters' not in chat_settings[chat_id]: chat_settings[chat_id]['filters'] = {}
    
    chat_settings[chat_id]['filters'][keyword] = reply_text
    save_settings(chat_settings)
    await msg.reply(f"✅ Permanent Filter added!\nWhen someone says <b>{keyword}</b>, I will reply with:\n{reply_text}\n\n<i>(My reply will auto-delete after 1 Hour)</i>")

@dp.message(Command("delfilter"))
async def cmd_delfilter(msg: types.Message):
    if msg.chat.type in ['private', 'channel']: return
    if not await is_admin(msg): return

    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.reply("❌ Format: <code>/delfilter keyword</code>")
        return

    keyword = args[1].lower()
    chat_id = str(msg.chat.id)

    if chat_id in chat_settings and 'filters' in chat_settings[chat_id] and keyword in chat_settings[chat_id]['filters']:
        del chat_settings[chat_id]['filters'][keyword]
        save_settings(chat_settings)
        await msg.reply(f"🗑️ Filter for <b>{keyword}</b> deleted.")
    else:
        await msg.reply("❌ Keyword not found in active filters.")

@dp.message(Command("delallfilters"))
async def cmd_delallfilters(msg: types.Message):
    if msg.chat.type in ['private', 'channel']: return
    if not await is_admin(msg): return

    chat_id = str(msg.chat.id)

    if chat_id in chat_settings and 'filters' in chat_settings[chat_id] and chat_settings[chat_id]['filters']:
        chat_settings[chat_id]['filters'].clear()
        save_settings(chat_settings)
        await msg.reply("🗑️ ✅ All active filters in this group have been successfully deleted.")
    else:
        await msg.reply("❌ No active filters found to delete.")

@dp.message(Command("filters"))
async def cmd_filters(msg: types.Message):
    if msg.chat.type in ['private', 'channel']: return
    if not await is_admin(msg): return
    
    chat_id = str(msg.chat.id)

    if chat_id in chat_settings and 'filters' in chat_settings[chat_id] and chat_settings[chat_id]['filters']:
        active_filters = "\n".join([f"• <code>{k}</code>" for k in chat_settings[chat_id]['filters'].keys()])
        await msg.reply(f"📋 <b>Permanent Active Filters:</b>\n{active_filters}")
    else:
        await msg.reply("No active filters in this group.")

# --- AUTO APPROVE ---
@dp.chat_join_request()
async def auto_approve_join_request(update: types.ChatJoinRequest):
    user_id, chat_id = update.from_user.id, str(update.chat.id)
    welcome_msg = chat_settings.get(chat_id, {}).get('welcome_msg')
    if welcome_msg and welcome_msg != "OFF":
        try: await bot.send_message(chat_id=user_id, text=welcome_msg)
        except Exception: pass 
    try: await update.approve()
    except Exception as e: logging.error(f"Failed to approve: {e}")

# --- LEFT MSG ---
@dp.chat_member()
async def on_chat_member_update(update: types.ChatMemberUpdated):
    user, chat_id = update.from_user, str(update.chat.id)
    if update.old_chat_member.status in ['member', 'administrator'] and update.new_chat_member.status in ['left', 'kicked']:
        default_left = "🌟 ALL DRAMA DIRECT FILES AVAILABLE 🗃️\n\nhttps://t.me/+amS1Q3R4_Qg5NjU1\nhttps://t.me/+amS1Q3R4_Qg5NjU1"
        final_msg = chat_settings.get(chat_id, {}).get('left_msg', default_left)
        if final_msg != "OFF":
            try: await bot.send_message(chat_id=user.id, text=final_msg)
            except Exception: pass 

# 👇 KEYWORD FILTER LISTENER 
@dp.message(F.text)
async def filter_handler(msg: types.Message):
    if msg.chat.type == 'private' or msg.text.startswith('/'): return
    chat_id = str(msg.chat.id)
    
    if chat_id in chat_settings and 'filters' in chat_settings[chat_id]:
        text_lower = msg.text.lower()
        
        for keyword, reply_text in chat_settings[chat_id]['filters'].items():
            if keyword in text_lower:
                sent_msg = await msg.reply(reply_text)
                
                if 'cleanup' not in chat_settings:
                    chat_settings['cleanup'] = []
                    
                chat_settings['cleanup'].append({
                    'chat_id': sent_msg.chat.id,
                    'message_id': sent_msg.message_id,
                    'delete_at': time.time() + 3600 
                })
                save_settings(chat_settings)
                break 

# 👇 BACKGROUND TASK: AUTO-DELETE REPLIES
async def cleanup_background_task():
    while True:
        await asyncio.sleep(60)
        if 'cleanup' not in chat_settings or not chat_settings['cleanup']:
            continue

        current_time = time.time()
        remaining_msgs = []
        changes_made = False

        for item in chat_settings['cleanup']:
            if current_time >= item['delete_at']:
                try:
                    await bot.delete_message(chat_id=item['chat_id'], message_id=item['message_id'])
                except Exception:
                    pass
                changes_made = True
            else:
                remaining_msgs.append(item)

        if changes_made:
            chat_settings['cleanup'] = remaining_msgs
            save_settings(chat_settings)

# --- SERVER ---
async def handle_ping(request): return web.Response(text="Running!")
async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080)))
    await site.start()

async def main():
    await start_dummy_server()
    asyncio.create_task(cleanup_background_task()) 
    logging.info("🤖 Safe Auto-Approve Bot is running...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
