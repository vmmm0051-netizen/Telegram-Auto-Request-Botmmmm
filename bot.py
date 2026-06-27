import os
import logging
import asyncio
import time
import random
from aiohttp import web
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from google import genai 

# --- LOGGING & CONFIG ---
logging.basicConfig(level=logging.INFO)
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
PORT = int(os.environ.get("PORT", 10000))

if not BOT_TOKEN or not MONGO_URI:
    raise RuntimeError('⚠️ BOT_TOKEN or MONGO_URI not set!')

# --- NEW GEMINI AI SETUP ---
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
else:
    ai_client = None
    logging.warning("⚠️ GEMINI_API_KEY missing! AI Chatbot feature won't work.")

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

# --- HELPER FUNCTIONS ---
async def get_chat_data(chat_id: str):
    data = await settings_col.find_one({"chat_id": chat_id})
    return data if data else {"chat_id": chat_id, "filters": {}, "cleanup": [], "welcome_msg": None, "left_msg": None}

async def update_chat_data(chat_id: str, update_dict: dict):
    await settings_col.update_one({"chat_id": chat_id}, {"$set": update_dict}, upsert=True)

async def is_admin(msg: types.Message) -> bool:
    if msg.sender_chat and str(msg.sender_chat.id) == str(msg.chat.id): return True
    try:
        member = await bot.get_chat_member(msg.chat.id, msg.from_user.id)
        return member.status in ['administrator', 'creator']
    except: return False

# --- COMMANDS ---
@dp.message(CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    if msg.chat.type != "private": return
    await state.clear()
    me = await bot.get_me()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ Add to Group', url=f'https://t.me/{me.username}?startgroup=true')],
        [InlineKeyboardButton(text='➕ Add to Channel', url=f'https://t.me/{me.username}?startchannel=start')]
    ])
    WELCOME_TEXT = (
        "╭━━━━━━━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "┃  🤖 <b>SAFE AUTO REQUEST + AI BOT</b>\n"
        "┃━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "┃  📌 <b>Commands:</b>\n"
        "┃  /help - Learn how to use this bot\n"
        "┃  /setwelcome - Set welcome message\n"
        "┃  /setleft - Set goodbye message\n"
        "┃  /cancel - Cancel process\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
    await msg.answer(WELCOME_TEXT, reply_markup=kb)

@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    if msg.chat.type != "private": return
    help_text = (
        "📖 <b>How to Use This Bot:</b>\n\n"
        "<b>1. Channel DMs (Welcome/Left):</b>\n"
        "• <code>/setwelcome</code> & <code>/setleft</code> to set msgs.\n"
        "• <code>/offwelcome</code> & <code>/offleft</code> to turn off.\n\n"
        "<b>2. Group Filters:</b>\n"
        "• <code>/addfilter &lt;word&gt; &lt;reply_text&gt;</code>\n"
        "• <code>/delfilter &lt;word&gt;</code>\n"
        "• <code>/filters</code>\n\n"
        "💡 <i>AI is active directly on every text! (Auto-deletes in 5 mins)</i>"
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
        return await msg.answer("❌ This is not a forwarded message from a channel.")
    
    channel_id = str(msg.forward_origin.chat.id)
    channel_title = msg.forward_origin.chat.title
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=msg.from_user.id)
        if member.status not in ['administrator', 'creator']:
            return await msg.answer("❌ You are not an Admin of this channel!")
    except Exception:
        return await msg.answer("❌ Please make the bot an Admin in your channel first.")

    data = await state.get_data()
    msg_type, action = data['msg_type'], data['action']
    
    if action == "off":
        await update_chat_data(channel_id, {msg_type: "OFF"})
        await msg.answer(f"✅ The message for '{channel_title}' has been turned <b>OFF</b>.")
        return await state.clear()

    await state.update_data(channel_id=channel_id, channel_title=channel_title)
    await state.set_state(MsgSetup.waiting_for_text)
    await msg.answer(f"✅ Channel verified: <b>{channel_title}</b>\n\n📝 Now, type and send your new custom Message.")

@dp.message(MsgSetup.waiting_for_text)
async def process_custom_msg(msg: types.Message, state: FSMContext):
    if not msg.text: return await msg.answer("❌ Please send text only.")
    data = await state.get_data()
    await update_chat_data(data['channel_id'], {data['msg_type']: msg.html_text})
    await msg.answer(f"✅ Message successfully set:\n\n{msg.html_text}")
    await state.clear()

@dp.message(Command("cancel"))
async def cmd_cancel(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("❌ Process cancelled.")

# --- GROUP FILTERS (WITH ADMIN OVERWRITE APPROVAL) ---
@dp.message(Command("addfilter"))
async def cmd_addfilter(msg: types.Message, state: FSMContext):
    if msg.chat.type in ['private', 'channel'] or not await is_admin(msg): return
    args = msg.text.split(maxsplit=2)
    if len(args) < 3: return await msg.reply("❌ Format: /addfilter keyword reply")
    
    keyword, reply_text = args[1].lower(), args[2]
    chat_data = await get_chat_data(str(msg.chat.id))
    is_update = keyword in chat_data.get('filters', {})
    
    if is_update:
        await state.update_data(pending_keyword=keyword, pending_reply=reply_text)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yes, Update", callback_data="filter_update_yes"),
             InlineKeyboardButton(text="❌ No, Cancel", callback_data="filter_update_no")]
        ])
        await msg.reply(f"⚠️ <b>Wait!</b> '<code>{keyword}</code>' ka filter pehle se mojood hai.\n\nKya aap ise naye message ke sath <b>OVERWRITE</b> karne ki permission dete hain?", reply_markup=kb)
    else:
        await settings_col.update_one({"chat_id": str(msg.chat.id)}, {"$set": {f"filters.{keyword}": reply_text}}, upsert=True)
        await msg.reply(f"✅ Naya Filter <b>{keyword}</b> successfully add ho gaya!")

@dp.callback_query(F.data.startswith("filter_update_"))
async def process_filter_update(call: CallbackQuery, state: FSMContext):
    try:
        member = await bot.get_chat_member(call.message.chat.id, call.from_user.id)
        if member.status not in ['administrator', 'creator']:
            return await call.answer("❌ Sirf Admin hi approve kar sakte hain!", show_alert=True)
    except: return await call.answer("❌ Error verifying admin.", show_alert=True)

    action = call.data.split("_")[-1]
    if action == "no":
        await state.clear()
        return await call.message.edit_text("❌ Update cancel kar diya gaya hai. Purana filter safe hai.")
        
    if action == "yes":
        data = await state.get_data()
        keyword, reply_text = data.get("pending_keyword"), data.get("pending_reply")
        if not keyword or not reply_text:
            return await call.message.edit_text("❌ Session expire ho gaya. Kripya naya command bhejein.")
            
        await settings_col.update_one({"chat_id": str(call.message.chat.id)}, {"$set": {f"filters.{keyword}": reply_text}}, upsert=True)
        await state.clear()
        await call.message.edit_text(f"✅ Approval Done! Filter <b>{keyword}</b> successfully UPDATE ho gaya!")

@dp.message(Command("delfilter"))
async def cmd_delfilter(msg: types.Message):
    if msg.chat.type in ['private', 'channel'] or not await is_admin(msg): return
    args = msg.text.split(maxsplit=1)
    if len(args) < 2: return
    await settings_col.update_one({"chat_id": str(msg.chat.id)}, {"$unset": {f"filters.{args[1].lower()}": ""}})
    await msg.reply(f"🗑️ Filter for <b>{args[1]}</b> deleted.")

@dp.message(Command("delallfilters"))
async def cmd_delallfilters(msg: types.Message):
    if msg.chat.type in ['private', 'channel'] or not await is_admin(msg): return
    await settings_col.update_one({"chat_id": str(msg.chat.id)}, {"$set": {"filters": {}}})
    await msg.reply("🗑️ ✅ All active filters deleted.")

@dp.message(Command("filters"))
async def cmd_filters(msg: types.Message):
    if msg.chat.type in ['private', 'channel'] or not await is_admin(msg): return
    chat_data = await get_chat_data(str(msg.chat.id))
    if chat_data.get('filters'):
        active_filters = "\n".join([f"• <code>{k}</code>" for k in chat_data['filters'].keys()])
        await msg.reply(f"📋 <b>Active Filters:</b>\n{active_filters}")
    else: await msg.reply("No active filters in this group.")

# --- AUTO APPROVE & LEFT MSG ---
@dp.chat_join_request()
async def auto_approve_join_request(update: types.ChatJoinRequest):
    user_id, chat_id = update.from_user.id, str(update.chat.id)
    chat_data = await get_chat_data(chat_id)
    welcome_msg = chat_data.get('welcome_msg')
    if welcome_msg and welcome_msg != "OFF":
        try: await bot.send_message(chat_id=user_id, text=welcome_msg)
        except: pass 
    try: await update.approve()
    except Exception as e: logging.error(f"Failed to approve: {e}")

@dp.chat_member()
async def on_chat_member_update(update: types.ChatMemberUpdated):
    user, chat_id = update.from_user, str(update.chat.id)
    if (update.old_chat_member.status in ['member', 'administrator'] and 
        update.new_chat_member.status in ['left', 'kicked']):
        
        chat_data = await get_chat_data(chat_id)
        default_left = "🌟 ALL DRAMA DIRECT FILES AVAILABLE 🗃️\n\nhttps://t.me/+amS1Q3R4_Qg5NjU1"
        final_msg = chat_data.get('left_msg') or default_left
        
        if final_msg != "OFF":
            try: await bot.send_message(chat_id=user.id, text=final_msg)
            except: pass

# --- MAIN LISTENER (FILTERS + BIG EMOJI + DIRECT AI) ---
@dp.message(F.text)
async def filter_handler(msg: types.Message):
    if msg.text.startswith('/'): return
    
    # 1. GROUP FILTERS LOGIC
    if msg.chat.type != 'private':
        chat_data = await get_chat_data(str(msg.chat.id))
        for kw, reply in chat_data.get('filters', {}).items():
            if kw in msg.text.lower():
                emoji_list = ["🔥", "❤️", "👍", "🎉", "🍿", "💯", "🚀", "😍", "👏"]
                try: await msg.react([types.ReactionTypeEmoji(emoji=random.choice(emoji_list))], is_big=True)
                except: pass
                
                sent = await msg.reply(f"<b>{reply}</b>", link_preview_options=types.LinkPreviewOptions(is_disabled=True))
                
                new_cleanup = chat_data.get('cleanup', []) + [{
                    "chat_id": sent.chat.id, 
                    "message_id": sent.message_id, 
                    "delete_at": time.time() + 3600,
                    "type": "filter"
                }]
                await update_chat_data(str(msg.chat.id), {"cleanup": new_cleanup})
                return 

    # 2. DIRECT AI CHATBOT LOGIC
    if not ai_client: return
    prompt = msg.text.strip()
    if not prompt: return
    
    await bot.send_chat_action(chat_id=msg.chat.id, action="typing")
    try:
        system_instruction = (
            "You are a friendly Movie & K-Drama expert chatbot in a Telegram Group. "
            "Provide quick summaries, story explanations, or recommendations. "
            "Keep answers engaging and strictly reply in Hinglish/Hindi language as requested by Indian users."
        )
        
                # 👇 BAS YAHAN MODEL KA NAAM UPDATE KARNA HAI
        def fetch_ai_reply():
            return ai_client.models.generate_content(
                model='gemini-2.5-flash',  # <-- Isey 2.0 se 2.5 kar dijiye
                contents=f"{system_instruction}\n\nUser Question: {prompt}"
            )

        
        response = await asyncio.to_thread(fetch_ai_reply)
        
        try:
            sent_ai = await msg.reply(response.text, parse_mode=ParseMode.MARKDOWN)
        except:
            sent_ai = await msg.reply(response.text, parse_mode=None)
            
        if sent_ai:
            ai_chat_data = await get_chat_data(str(msg.chat.id))
            new_ai_cleanup = ai_chat_data.get('cleanup', []) + [{
                "chat_id": sent_ai.chat.id,
                "message_id": sent_ai.message_id,
                "delete_at": time.time() + 300, 
                "type": "ai"
            }]
            await update_chat_data(str(msg.chat.id), {"cleanup": new_ai_cleanup})
            
    except Exception as e:
        logging.error(f"AI Generation Error: {e}")

# --- BACKGROUND CLEANUP TASK (EDIT FILTERS / DELETE AI) ---
async def cleanup_task():
    while True:
        await asyncio.sleep(60)
        async for chat in settings_col.find({"cleanup": {"$not": {"$size": 0}}}):
            valid = []
            for item in chat.get('cleanup', []):
                if time.time() >= item['delete_at']:
                    try:
                        if item.get('type') == 'ai':
                            await bot.delete_message(chat_id=item['chat_id'], message_id=item['message_id'])
                        else:
                            await bot.edit_message_text(
                                chat_id=item['chat_id'], 
                                message_id=item['message_id'],
                                text="💖 Just send the title, and I'll get it for you instantly! 👇"
                            )
                    except: pass
                else: valid.append(item)
            await update_chat_data(chat['chat_id'], {"cleanup": valid})

# --- RENDER WEB SERVER (ANTI-CRASH) ---
async def handle_ping(request): 
    return web.Response(text="Bot is running smoothly on Render with Gemini 2.0!")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

# --- MAIN BOOT ---
async def main():
    await bot.delete_webhook(drop_pending_updates=True) 
    await start_dummy_server()
    asyncio.create_task(cleanup_task())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
