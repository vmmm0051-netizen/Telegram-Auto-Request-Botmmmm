import os
import logging
import asyncio
import time
import random
import re
import base64
from aiohttp import web
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery, ChatJoinRequest, ChatMemberUpdated
from pyrogram.errors import FloodWait, MessageNotModified

# --- LOGGING & CONFIG ---
logging.basicConfig(level=logging.INFO)
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
API_ID = int(os.getenv('API_ID', 0))      # Tor Telegram API ID (.env te thakte hobe)
API_HASH = os.getenv('API_HASH', '')    # Tor Telegram API Hash (.env te thakte hobe)
PORT = int(os.environ.get("PORT", 10000))

# ⚠️ YAHAN APNA MAIN CHANNEL LINK AUR BOT USERNAME DAALEIN
CHANNEL_LINK = "https://t.me/YOUR_CHANNEL_USERNAME"
BOT_USERNAME = "YOUR_BOT_USERNAME" # Bot er username bina @ chara

if not BOT_TOKEN or not MONGO_URI or not API_ID or not API_HASH:
    raise RuntimeError('⚠️ BOT_TOKEN, MONGO_URI, API_ID, or API_HASH not set!')

bot = Client("filter_batch_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, parse_mode=enums.ParseMode.HTML)

# --- MONGODB SETUP ---
client = AsyncIOMotorClient(MONGO_URI)
db = client.bot_database
settings_col = db.chat_settings

# --- BATCH SYSTEM HELPERS ---
LINK_REGEX = re.compile(r'https://t\.me/(?:c/)?(.*)/(\d+)')
user_states = {}

def encode_id(chat_id, first_id, last_id):
    raw = f"{chat_id}:{first_id}:{last_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

def decode_id(token):
    try:
        padding = 4 - (len(token) % 4)
        if padding != 4: token += "=" * padding
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        return raw.split(":")
    except: return None

# --- HELPER FUNCTIONS ---
async def get_chat_data(chat_id: str):
    data = await settings_col.find_one({"chat_id": chat_id})
    return data if data else {"chat_id": chat_id, "filters": {}, "cleanup": [], "welcome_msg": None, "left_msg": None}

async def update_chat_data(chat_id: str, update_dict: dict):
    await settings_col.update_one({"chat_id": chat_id}, {"$set": update_dict}, upsert=True)

async def is_admin(client, msg: Message) -> bool:
    if msg.sender_chat and str(msg.sender_chat.id) == str(msg.chat.id): return True
    try:
        member = await client.get_chat_member(msg.chat.id, msg.from_user.id)
        return member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except: return False

def get_greeting():
    ist_time = time.time() + (5.5 * 3600)
    hour = time.gmtime(ist_time).tm_hour
    if hour < 12: return "ɢᴏᴏᴅ ᴍᴏʀɴɪɴɢ 🌞"
    elif hour < 17: return "ɢᴏᴏᴅ ᴀꜰᴛᴇʀɴᴏᴏɴ 🌤️"
    elif hour < 20: return "ɢᴏᴏᴅ ᴇᴠᴇɴɪɴɢ 🌥️"
    else: return "ɢᴏᴏᴅ ɴɪɢʜᴛ 🌙"

# ==========================================
# 1. BATCH GENERATOR COMMANDS
# ==========================================
@bot.on_message(filters.command("batch") & filters.private)
async def cmd_batch(client: Client, msg: Message):
    user_states[msg.from_user.id] = {"state": "waiting_for_first"}
    await msg.reply_text("<b>Forward The Batch First Message From your Batch Channel (With Forward Tag).. or Give Me Batch First Message link from your batch channel</b>")

@bot.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(client: Client, msg: Message):
    user_states.pop(msg.from_user.id, None)
    await msg.reply_text("❌ Process cancelled.")

# ==========================================
# 2. STATE MANAGER FOR PRIVATE CHAT
# ==========================================
@bot.on_message(filters.private & ~filters.command(["start", "batch", "help", "cancel", "setwelcome", "setleft", "offwelcome", "offleft"]))
async def private_state_manager(client: Client, msg: Message):
    user_id = msg.from_user.id
    state_data = user_states.get(user_id)
    if not state_data: return

    state = state_data["state"]

    # WELCOME / LEFT CONFIG STATES
    if state == "waiting_for_forward":
        if not msg.forward_from_chat or msg.forward_from_chat.type != enums.ChatType.CHANNEL:
            return await msg.reply_text("❌ This is not a forwarded message from a channel.")
        
        channel_id = str(msg.forward_from_chat.id)
        channel_title = msg.forward_from_chat.title
        try:
            member = await client.get_chat_member(chat_id=int(channel_id), user_id=user_id)
            if member.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
                return await msg.reply_text("❌ You are not an Admin of this channel!")
        except Exception:
            return await msg.reply_text("❌ Please make the bot an Admin in your channel first.")

        msg_type, action = state_data['msg_type'], state_data['action']
        if action == "off":
            await update_chat_data(channel_id, {msg_type: "OFF"})
            await msg.reply_text(f"✅ The message for '{channel_title}' has been turned <b>OFF</b>.")
            return user_states.pop(user_id, None)

        state_data.update({"state": "waiting_for_text", "channel_id": channel_id, "channel_title": channel_title})
        await msg.reply_text(f"✅ Channel verified: <b>{channel_title}</b>\n\n📝 Now, type and send your new custom Message.")

    elif state == "waiting_for_text":
        if not msg.text: return await msg.reply_text("❌ Please send text only.")
        await update_chat_data(state_data['channel_id'], {state_data['msg_type']: msg.text})
        await msg.reply_text(f"✅ Message successfully set:\n\n{msg.text}")
        user_states.pop(user_id, None)

    # BATCH GENERATOR STATES
    elif state == "waiting_for_first":
        msg_id, chat_id = None, None
        if msg.forward_from_chat and msg.forward_from_chat.type == enums.ChatType.CHANNEL:
            chat_id = str(msg.forward_from_chat.id)
            msg_id = msg.forward_from_message_id
        elif msg.text and "t.me" in msg.text:
            match = LINK_REGEX.search(msg.text)
            if match:
                chat_id_or_username = match.group(1)
                msg_id = int(match.group(2))
                chat_id = f"-100{chat_id_or_username}" if chat_id_or_username.isdigit() else chat_id_or_username

        if not msg_id or not chat_id:
            return await msg.reply_text("❌ Invalid Input! Please forward a message or send a valid Telegram message link.")

        state_data.update({"state": "waiting_for_last", "chat_id": chat_id, "first_id": msg_id})
        await msg.reply_text("<b>Forward The Batch Last Message From Your Batch Channel (With Forward Tag).. or Give Me Batch last message link from your batch channel</b>")

    elif state == "waiting_for_last":
        msg_id = None
        if msg.forward_origin and msg.forward_origin.type == 'channel':
            msg_id = msg.forward_origin.message_id
        elif msg.text and "t.me" in msg.text:
            match = LINK_REGEX.search(msg.text)
            if match: msg_id = int(match.group(2))

        if not msg_id:
            return await msg.reply_text("❌ Invalid Input! Please forward a message or send a valid Telegram message link.")

        chat_id, first_id, last_id = state_data['chat_id'], state_data['first_id'], msg_id
        if first_id > last_id: first_id, last_id = last_id, first_id

        token = encode_id(chat_id, first_id, last_id)
        batch_link = f"https://t.me/{BOT_USERNAME}?start=batch_{token}"
        await msg.reply_text(f"✅ <b>Here is your Batch Link:</b>\n\n<code>{batch_link}</code>")
        user_states.pop(user_id, None)

# --- CHANNEL DM SETUP MANAGEMENT ---
@bot.on_message(filters.command(["setwelcome", "setleft", "offwelcome", "offleft"]) & filters.private)
async def start_setting_msg(client: Client, msg: Message):
    cmd = msg.command[0]
    msg_type = "left_msg" if cmd in ["setleft", "offleft"] else "welcome_msg"
    action = "off" if cmd.startswith("off") else "set"
    user_states[msg.from_user.id] = {"state": "waiting_for_forward", "msg_type": msg_type, "action": action}
    await msg.reply_text("📢 Please <b>Forward</b> any message from your Channel here.")

# ==========================================
# 3. START COMMAND WITH VIP FILE SENDER
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def cmd_start(client: Client, msg: Message):
    user_states.pop(msg.from_user.id, None)
    
    # BATCH FILE SENDING PROCESS
    if len(msg.command) > 1 and msg.command[1].startswith("batch_"):
        token = msg.command[1].replace("batch_", "")
        data = decode_id(token)
        
        if data and len(data) == 3:
            chat_id, first_id, last_id = data[0], int(data[1]), int(data[2])
            try: chat_id = int(chat_id)
            except ValueError: pass

            wait_msg = await msg.reply_text("⏳ <i>Sending your files, please wait...</i>")
            vip_button = InlineKeyboardMarkup([[InlineKeyboardButton("📌 JOIN UPDATES CHANNEL 📌", url=CHANNEL_LINK)]])
            
            for m_id in range(first_id, last_id + 1):
                try:
                    # Pyrogram e direct file name check kora jay natively! 😎
                    tg_msg = await client.get_messages(chat_id, m_id)
                    if tg_msg.empty: continue
                    
                    file_name = "🎬 Movie/Series File"
                    if tg_msg.document: file_name = tg_msg.document.file_name
                    elif tg_msg.video: file_name = tg_msg.video.file_name
                    elif tg_msg.audio: file_name = tg_msg.audio.file_name
                    
                    # 🔥 EXACT VIP NEELA CAPTION 🔥
                    vip_caption = (
                        f"<b><a href='{CHANNEL_LINK}'>{file_name}</a></b>\n\n"
                        f"<b>⚜️ Powered By : <a href='{CHANNEL_LINK}'>[ iP Update ]</a></b>"
                    )
                    
                    await client.copy_message(
                        chat_id=msg.chat.id,
                        from_chat_id=chat_id,
                        message_id=m_id,
                        caption=vip_caption,
                        reply_markup=vip_button
                    )
                    await asyncio.sleep(0.5)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                except Exception:
                    pass
            return await wait_msg.delete()
            
    # DEFAULT START RESPONSE
    me = await client.get_me()
    greeting = get_greeting()
    user_name = msg.from_user.first_name.upper() if msg.from_user.first_name else "USER"
    bot_name = me.first_name.upper() if me.first_name else "BOT"
    
    caption = (
        f"🚩 <b>JAI SHRI RAM</b> 🚩\n\n"
        f"<b>HEY {user_name}</b>, <b>{greeting}</b>\n\n"
        f"🤖 <b>ɪ ᴀᴍ {bot_name}, ᴛʜᴇ ᴍᴏꜱᴛ ᴘᴏᴡᴇʀꜰᴜʟ ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ ʙᴏᴛ ᴡɪᴛʜ ᴘʀᴇᴍɪᴜᴍ ꜰᴇᴀᴛᴜʀᴇꜱ.</b>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('🔰 ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ 🔰', url=f'https://t.me/{me.username}?startgroup=true')],
        [InlineKeyboardButton('ʜᴇʟᴘ 📢', callback_data='help_menu'), InlineKeyboardButton('ᴀʙᴏᴜᴛ 📖', callback_data='about_menu')],
        [InlineKeyboardButton('ᴛᴏᴘ ꜱᴇᴀʀᴄʜɪɴɢ ⭐', callback_data='top_search'), InlineKeyboardButton('ᴜᴘɢʀᴀᴅᴇ 🎟️', callback_data='upgrade_menu')],
        [InlineKeyboardButton('➕ ᴀᴅᴅ ᴛᴏ ᴄʜᴀɴɴᴇʟ ➕', url=f'https://t.me/{me.username}?startchannel=start')]
    ])
    IMAGE_URL = "https://images.unsplash.com/photo-1534447677768-be436bb09401?w=800"
    try: await msg.reply_photo(photo=IMAGE_URL, caption=caption, reply_markup=kb)
    except: await msg.reply_text(caption, reply_markup=kb)

# --- CALLBACK MENUS ---
@bot.on_callback_query()
async def cb_handlers(client: Client, call: CallbackQuery):
    if call.data == "help_menu":
        help_text = (
            "📖 <b>Full Command & Feature Guide:</b>\n\n"
            "📢 <b>1. Channel DMs (Welcome/Goodbye):</b>\n• <code>/setwelcome</code> & <code>/setleft</code>\n• <code>/offwelcome</code> & <code>/offleft</code>\n\n"
            "🗃️ <b>2. Group Filters Management:</b>\n• <code>/addfilter [keyword] [reply text]</code>\n• <code>/delfilter [keyword]</code>\n• <code>/delallfilters</code>\n• <code>/filters</code>\n\n"
            "⚡ <b>3. Premium Features (Auto-Active):</b>\n• <b>Auto-Approve:</b> Channel requests approved instantly.\n• <b>Exact Match:</b> Strict word boundary filter triggers.\n• <b>Big Emoji Reaction:</b> Pop-up animations on triggers.\n• <b>Auto-Edit:</b> Filter replies edit after 24 hours."
        )
        back_kb = InlineKeyboardMarkup([[InlineKeyboardButton('🔙 ʙᴀᴄᴋ', callback_data='start_menu')]])
        try: await call.message.edit_caption(caption=help_text, reply_markup=back_kb)
        except: pass
    elif call.data == "about_menu":
        me = await client.get_me()
        about_text = (
            f"🤖 <b>ᴀʙᴏᴜᴛ {me.first_name.upper()}</b>\n\n<b>• ᴅᴇᴠᴇʟᴏᴘᴇʀ:</b> Admin\n<b>• ʟᴀɴɢᴜᴀɢᴇ:</b> Python 3\n<b>• ꜰʀᴀᴍᴇᴡᴏʀᴋ:</b> Pyrogram\n<b>• ᴅᴀᴛᴀʙᴀꜱᴇ:</b> MongoDB\n\n<i>This bot provides powerful auto-request approval, dynamic EXACT keyword filtering with overwrite protection, and 24-hour auto-edit features for Telegram Groups & Channels.</i>"
        )
        back_kb = InlineKeyboardMarkup([[InlineKeyboardButton('🔙 ʙᴀᴄᴋ', callback_data='start_menu')]])
        try: await call.message.edit_caption(caption=about_text, reply_markup=back_kb)
        except: pass
    elif call.data == "top_search":
        await call.answer("⭐ Top Searching feature coming soon!", show_alert=True)
    elif call.data == "upgrade_menu":
        await call.answer("🎟️ Upgrade feature coming soon!", show_alert=True)
    elif call.data == "start_menu":
        me = await client.get_me()
        caption = f"🚩 <b>JAI SHRI RAM</b> 🚩\n\n<b>HEY {call.from_user.first_name.upper()}</b>, <b>{get_greeting()}</b>\n\n🤖 <b>ɪ ᴀᴍ {me.first_name.upper()}, ᴛʜᴇ ᴍᴏꜱᴛ ᴘᴏᴡᴇʀꜰᴜʟ ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ ʙᴏᴛ ᴡɪᴛʜ ᴘʀᴇᴍɪᴜᴍ ꜰᴇᴀᴛᴜʀᴇꜱ.</b>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('🔰 ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ 🔰', url=f'https://t.me/{me.username}?startgroup=true')],
            [InlineKeyboardButton('ʜᴇʟᴘ 📢', callback_data='help_menu'), InlineKeyboardButton('ᴀʙᴏᴜᴛ 📖', callback_data='about_menu')],
            [InlineKeyboardButton('ᴛᴏᴘ ꜱᴇᴀʀᴄʜɪɴɢ ⭐', callback_data='top_search'), InlineKeyboardButton('... 🎟️', callback_data='upgrade_menu')],
            [InlineKeyboardButton('➕ ᴀᴅᴅ ᴛᴏ ᴄʜᴀɴɴᴇʟ ➕', url=f'https://t.me/{me.username}?startchannel=start')]
        ])
        try: await call.message.edit_caption(caption=caption, reply_markup=kb)
        except: pass

    # FILTER OVERWRITE APPROVAL SYSTEM
    elif call.data.startswith("filter_update_"):
        if not await is_admin(client, call.message):
            return await call.answer("❌ Sirf Admin hi approve kar sakte hain!", show_alert=True)
        action = call.data.split("_")[-1]
        chat_id = str(call.message.chat.id)
        chat_data = await get_chat_data(chat_id)
        pending = chat_data.get("pending_filter")
        
        if not pending: return await call.message.edit_text("❌ Session expired.")
        if action == "no":
            await settings_col.update_one({"chat_id": chat_id}, {"$unset": {"pending_filter": ""}})
            return await call.message.edit_text("❌ Update cancel kar diya gaya hai.")
        if action == "yes":
            kw, reply = pending["keyword"], pending["reply_text"]
            await settings_col.update_one({"chat_id": chat_id}, {"$set": {f"filters.{kw}": reply}, "$unset": {"pending_filter": ""}})
            await call.message.edit_text(f"✅ Filter <b>{kw}</b> successfully UPDATE ho gaya!")

@bot.on_message(filters.command("help") & filters.private)
async def cmd_help(client: Client, msg: Message):
    await msg.reply_text("📖 <b>Full Command Guide:</b>\n\n📢 <b>1. Channel DMs:</b>\n• <code>/setwelcome</code> & <code>/setleft</code>\n• <code>/offwelcome</code> & <code>/offleft</code>\n\n🗃️ <b>2. Group Filters:</b>\n• <code>/addfilter [word] [reply]</code>\n• <code>/delfilter [word]</code>\n• <code>/filters</code>")

# ==========================================
# 4. GROUP FILTERS MANAGEMENT
# ==========================================
@bot.on_message(filters.command("addfilter") & filters.group)
async def cmd_addfilter(client: Client, msg: Message):
    if not await is_admin(client, msg): return
    args = msg.text.split(maxsplit=2)
    if len(args) < 3: return await msg.reply_text("❌ Format: /addfilter keyword reply")
    
    keyword, reply_text = args[1].lower(), args[2]
    chat_id = str(msg.chat.id)
    chat_data = await get_chat_data(chat_id)
    
    if keyword in chat_data.get('filters', {}):
        await settings_col.update_one({"chat_id": chat_id}, {"$set": {"pending_filter": {"keyword": keyword, "reply_text": reply_text}}}, upsert=True)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Yes, Update", callback_data="filter_update_yes"), InlineKeyboardButton("❌ No, Cancel", callback_data="filter_update_no")]])
        await msg.reply_text(f"⚠️ <b>Wait!</b> '<code>{keyword}</code>' ka filter pehle se mojood hai. Overwrite?", reply_markup=kb)
    else:
        await settings_col.update_one({"chat_id": chat_id}, {"$set": {f"filters.{keyword}": reply_text}}, upsert=True)
        await msg.reply_text(f"✅ Naya Filter <b>{keyword}</b> added!")

@bot.on_message(filters.command("delfilter") & filters.group)
async def cmd_delfilter(client: Client, msg: Message):
    if not await is_admin(client, msg): return
    args = msg.text.split(maxsplit=1)
    if len(args) < 2: return
    await settings_col.update_one({"chat_id": str(msg.chat.id)}, {"$unset": {f"filters.{args[1].lower()}": ""}})
    await msg.reply_text(f"🗑️ Filter for <b>{args[1]}</b> deleted.")

@bot.on_message(filters.command("delallfilters") & filters.group)
async def cmd_delallfilters(client: Client, msg: Message):
    if not await is_admin(client, msg): return
    await settings_col.update_one({"chat_id": str(msg.chat.id)}, {"$set": {"filters": {}}})
    await msg.reply_text("🗑️ ✅ All active filters deleted.")

@bot.on_message(filters.command("filters") & filters.group)
async def cmd_filters(client: Client, msg: Message):
    if not await is_admin(client, msg): return
    chat_data = await get_chat_data(str(msg.chat.id))
    if chat_data.get('filters'):
        active_filters = "\n".join([f"• <code>{k}</code>" for k in chat_data['filters'].keys()])
        await msg.reply_text(f"📋 <b>Active Filters:</b>\n{active_filters}")
    else: await msg.reply_text("No active filters.")

# ==========================================
# 5. AUTO CHAT JOIN APPROVAL & DM LOGIC
# ==========================================
@bot.on_chat_join_request()
async def auto_approve_join_request(client: Client, request: ChatJoinRequest):
    user_id, chat_id = request.from_user.id, str(request.chat.id)
    chat_data = await get_chat_data(chat_id)
    welcome_msg = chat_data.get('welcome_msg')
    if welcome_msg and welcome_msg != "OFF":
        try: await client.send_message(chat_id=user_id, text=welcome_msg)
        except: pass 
    try: await request.approve()
    except Exception as e: logging.error(f"Failed to approve: {e}")

@bot.on_chat_member_updated()
async def on_chat_member_update(client: Client, update: ChatMemberUpdated):
    if not update.old_chat_member or not update.new_chat_member: return
    if (update.old_chat_member.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER] and 
        update.new_chat_member.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.RESTRICTED]):
        
        user = update.new_chat_member.user
        chat_id = str(update.chat.id)
        chat_data = await get_chat_data(chat_id)
        final_msg = chat_data.get('left_msg')
