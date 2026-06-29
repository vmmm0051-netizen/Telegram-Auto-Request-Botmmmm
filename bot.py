import os
import logging
import asyncio
import time
import random
import re
import base64
import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery, ChatJoinRequest, ChatMemberUpdated
from pyrogram.errors import FloodWait, UserNotParticipant, ChatAdminRequired

# --- LOGGING & CONFIG ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
API_ID = os.getenv('API_ID', '0')      
API_HASH = os.getenv('API_HASH', '')    
PORT = int(os.environ.get("PORT", 10000))

# ⚠️ LINKS AUR BOT USERNAME
FILE_CAPTION_LINK = "https://t.me/ASKORENDRAMA"       
UPDATE_CHANNEL_LINK = "https://t.me/ASKORENDRAMA"   
BOT_USERNAME = "Channelpsotsearchbot" 

# ⚠️ FORCE SUB CHANNELS (Yahan apne channels daalein)
FSUB_CHANNEL_1 = -1000000000000  # Pehle Channel ki ID yahan daalein (Minus lagana zaroori hai)
FSUB_CHANNEL_2 = -1000000000000  # Dusre Channel ki ID yahan daalein
FSUB_LINK_1 = "https://t.me/AapkaPehlaChannel"   # Pehle Channel ka invite link
FSUB_LINK_2 = "https://t.me/AapkaDusraChannel"   # Dusre Channel ka invite link                             

# Initialize Client
bot = Client("filter_batch_bot", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN, parse_mode=enums.ParseMode.HTML)

# --- MONGODB SETUP ---
client = AsyncIOMotorClient(MONGO_URI)
db = client.bot_database
settings_col = db.chat_settings
autofilter_col = db.autofilter_data  

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

# --- SMART FSUB MISSING CHANNELS FINDER (FIXED CACHE ISSUE) ---
async def get_missing_channels(client, user_id):
    missing = []
    channels = [
        {"id": FSUB_CHANNEL_1, "link": FSUB_LINK_1, "name": "Channel 1"},
        {"id": FSUB_CHANNEL_2, "link": FSUB_LINK_2, "name": "Channel 2"}
    ]
    for ch in channels:
        try:
            target_chat = int(ch["id"])
            member = await client.get_chat_member(target_chat, user_id)
            if member.status in [enums.ChatMemberStatus.BANNED, enums.ChatMemberStatus.LEFT]:
                missing.append(ch)
        except UserNotParticipant:
            missing.append(ch)
        except ChatAdminRequired:
            logging.error(f"❌ BOT ADMIN NAHI HAI Channel {ch['id']} mein!")
            missing.append(ch)
        except Exception as e:
            logging.error(f"⚠️ FSUB Cache/API Error for {ch['id']}: {e}")
            pass
    return missing

# --- FSUB TRY AGAIN BUTTON HANDLER ---
@bot.on_callback_query(filters.regex(r"^fsub_(.*)"))
async def fsub_callback(client: Client, call: CallbackQuery):
    token = call.matches[0].group(1)
    missing_channels = await get_missing_channels(client, call.from_user.id)
    if missing_channels:
        return await call.answer("❌ Please join all required channels first!", show_alert=True)
    
    await call.message.delete()
    call.message.from_user = call.from_user
    call.message.command = ["start", f"batch_{token}"]
    await cmd_start(client, call.message)

# ==========================================
# 1. BATCH GENERATOR & CUSTOM TIME COMMANDS
# ==========================================
@bot.on_message(filters.command("batch") & filters.private)
async def cmd_batch(client: Client, msg: Message):
    user_states[msg.from_user.id] = {"state": "waiting_for_first"}
    await msg.reply_text("<b>Forward The Batch First Message From your Batch Channel (With Forward Tag).. or Give Me Batch First Message link from your batch channel</b>")

@bot.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(client: Client, msg: Message):
    user_states.pop(msg.from_user.id, None)
    await msg.reply_text("❌ Process cancelled.")

@bot.on_message(filters.command("settime") & filters.private)
async def cmd_set_time(client: Client, msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.reply_text("❌ <b>Format:</b> <code>/settime <seconds></code>\n\n<b>Example:</b>\n• <code>/settime 60</code> (1 Minute)\n• <code>/settime 600</code> (10 Minutes)\n• <code>/settime 3600</code> (1 Hour)")
    
    try:
        seconds = int(args[1])
        if seconds < 5:
            return await msg.reply_text("❌ Kripya kam se kam 5 seconds ka time set karein!")
        
        await settings_col.update_one(
            {"chat_id": "GLOBAL_CONFIG"},
            {"$set": {"batch_delete_time": seconds}},
            upsert=True
        )
        
        time_text = f"{seconds} seconds"
        if seconds >= 60:
            time_text = f"{seconds // 60} minutes"
            
        await msg.reply_text(f"✅ <b>Batch Delete Time successfully update ho gaya hai!</b>\nAb se /batch link ki saari files <b>{time_text}</b> ke baad automatically delete ho jayengi.")
    except ValueError:
        await msg.reply_text("❌ Kripya ek valid number daalein (sirf digits/numbers)!")

# ==========================================
# 2. STATE MANAGER FOR PRIVATE CHAT
# ==========================================
@bot.on_message(filters.private & ~filters.command(["start", "batch", "help", "cancel", "setwelcome", "setleft", "offwelcome", "offleft", "settime"]))
async def private_state_manager(client: Client, msg: Message):
    user_id = msg.from_user.id
    state_data = user_states.get(user_id)
    if not state_data: return

    state = state_data["state"]

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
        if msg.forward_from_chat and msg.forward_from_chat.type == enums.ChatType.CHANNEL:
            msg_id = msg.forward_from_message_id
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
# 3. START COMMAND WITH FSUB & TIMED SENDER
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def cmd_start(client: Client, msg: Message):
    user_states.pop(msg.from_user.id, None)
    
    if len(msg.command) > 1 and msg.command[1].startswith("batch_"):
        token = msg.command[1].replace("batch_", "")
        
        # 👇 NAYA VIP DYNAMIC FSUB CHECK 👇
        missing_channels = await get_missing_channels(client, msg.from_user.id)
        if missing_channels:
            user_link = f"<a href='tg://user?id={msg.from_user.id}'>{msg.from_user.first_name}</a>"
            fsub_text = f"<i>Hey {user_link}\n\nPlease Join My Update Channel(s) To Use Me!</i>"
            
            fsub_buttons = []
            for ch in missing_channels:
                fsub_buttons.append([InlineKeyboardButton(f"Join {ch['name']}", url=ch["link"])])
            
            fsub_buttons.append([InlineKeyboardButton("♻️ Try Again", callback_data=f"fsub_{token}")])
            return await msg.reply_text(fsub_text, reply_markup=InlineKeyboardMarkup(fsub_buttons))
        # 👆 DYNAMIC FSUB CHECK KHATAM 👆

        data = decode_id(token)
        
        if data and len(data) == 3:
            chat_id, first_id, last_id = data[0], int(data[1]), int(data[2])
            try: chat_id = int(chat_id)
            except ValueError: pass

            wait_msg = await msg.reply_text("⏳ <i>Sending your files, please wait...</i>")
            
            global_config = await settings_col.find_one({"chat_id": "GLOBAL_CONFIG"})
            delete_delay = global_config.get("batch_delete_time", 600) if global_config else 600
            
            vip_button = InlineKeyboardMarkup([
                [InlineKeyboardButton("📌 JOIN UPDATE CHANNEL 📌", url=UPDATE_CHANNEL_LINK)]
            ])
            
            sent_ids = []
            for m_id in range(first_id, last_id + 1):
                try:
                    tg_msg = await client.get_messages(chat_id, m_id)
                    if tg_msg.empty: continue
                    
                    if tg_msg.document or tg_msg.video or tg_msg.audio:
                        file_name = "🎬 Movie/Series File"
                        if tg_msg.document and tg_msg.document.file_name: 
                            file_name = tg_msg.document.file_name
                        elif tg_msg.video and tg_msg.video.file_name: 
                            file_name = tg_msg.video.file_name
                        elif tg_msg.audio and tg_msg.audio.file_name: 
                            file_name = tg_msg.audio.file_name
                        
                        vip_caption = (
                            f"<b><a href='{FILE_CAPTION_LINK}'>{file_name}</a></b>\n\n"
                            f"<b>⚜️ Powered By : @ASKORENDRAMA</b>"
                        )
                        
                        sent = await client.copy_message(
                            chat_id=msg.chat.id,
                            from_chat_id=chat_id,
                            message_id=m_id,
                            caption=vip_caption,
                            reply_markup=vip_button
                        )
                        sent_ids.append(sent.id)
                    else:
                        sent = await client.copy_message(
                            chat_id=msg.chat.id,
                            from_chat_id=chat_id,
                            message_id=m_id
                        )
                        sent_ids.append(sent.id)
                    
                    await asyncio.sleep(0.5)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                except Exception:
                    pass
            
            await wait_msg.delete()

            if sent_ids:
                time_text = f"{delete_delay} seconds"
                if delete_delay >= 60:
                    time_text = f"{delete_delay // 60} minutes"
                
                alert_text = (
                    "⚠️ <u><b>Important:</b></u>\n\n"
                    f"<i>All Messages will be deleted after <b>{time_text}</b>. Please save or forward these "
                    "messages to your <b>personal saved messages</b> to avoid losing them!</i>"
                )
                
                alert_button = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📟 UPDATE CHANNEL", url=UPDATE_CHANNEL_LINK)]
                ])
                
                alert = await msg.reply_text(
                    text=alert_text,
                    reply_markup=alert_button
                )
                sent_ids.append(alert.id)
                
                user_chat_id = str(msg.chat.id)
                user_data = await get_chat_data(user_chat_id)
                cleanup_items = user_data.get('cleanup', [])
                
                for s_id in sent_ids:
                    cleanup_items.append({
                        "chat_id": msg.chat.id,
                        "message_id": s_id,
                        "delete_at": time.time() + delete_delay,
                        "action": "delete"
                    })
                await update_chat_data(user_chat_id, {"cleanup": cleanup_items})
            return
            
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
@bot.on_callback_query(~filters.regex(r"^fsub_(.*)"))
async def cb_handlers(client: Client, call: CallbackQuery):
    if call.data == "help_menu":
        help_text = (
            "📖 <b>Full Command & Feature Guide:</b>\n\n"
            "📢 <b>1. Channel DMs (Welcome/Goodbye):</b>\n• <code>/setwelcome</code> & <code>/setleft</code>\n• <code>/offwelcome</code> & <code>/offleft</code>\n\n"
            "🗃️ <b>2. Group Filters Management:</b>\n• <code>/addfilter [keyword] | [reply link]</code>\n• <code>/delfilter [keyword]</code>\n• <code>/delallfilters</code>\n• <code>/filters</code>\n\n"
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
            [InlineKeyboardButton('ᴛᴏᴘ ꜱᴇᴀʀᴄʜɪɴɢ ⭐', callback_data='top_search'), InlineKeyboardButton('ᴜᴘɢʀᴀᴅᴇ 🎟️', callback_data='upgrade_menu')],
            [InlineKeyboardButton('➕ ᴀᴅᴅ ᴛᴏ ᴄʜᴀɴɴᴇʟ ➕', url=f'https://t.me/{me.username}?startchannel=start')]
        ])
        try: await call.message.edit_caption(caption=caption, reply_markup=kb)
        except: pass
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
    await msg.reply_text("📖 <b>Full Command Guide:</b>\n\n📢 <b>1. Channel DMs:</b>\n• <code>/setwelcome</code> & <code>/setleft</code>\n• <code>/offwelcome</code> & <code>/offleft</code>\n\n🗃️ <b>2. Group Filters:</b>\n• <code>/addfilter [word] | [reply]</code>\n• <code>/delfilter [word]</code>\n• <code>/filters</code>\n\n⏱️ <b>3. Custom Timer:</b>\n• <code>/settime <seconds></code>")

# ==========================================
# 4. GROUP FILTERS MANAGEMENT
# ==========================================
@bot.on_message(filters.command("addfilter") & filters.group)
async def cmd_addfilter(client: Client, msg: Message):
    if not await is_admin(client, msg): return
    
    if "|" not in msg.text:
        return await msg.reply_text("❌ <b>Sahi Format:</b> <code>/addfilter keyword | reply link</code>\n\n<b>Example:</b>\n<code>/addfilter my demon k drama | https://t.me/ASKORENDRAMA/123</code>")
    
    text = msg.text.replace("/addfilter", "", 1).strip()
    keyword, reply_text = text.split("|", 1)
    keyword = keyword.strip().lower()
    reply_text = reply_text.strip()
    
    chat_id = str(msg.chat.id)
    chat_data = await get_chat_data(chat_id)
    
    if keyword in chat_data.get('filters', {}):
        await settings_col.update_one({"chat_id": chat_id}, {"$set": {"pending_filter": {"keyword": keyword, "reply_text": reply_text}}}, upsert=True)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Yes, Update", callback_data="filter_update_yes"), InlineKeyboardButton("❌ No, Cancel", callback_data="filter_update_no")]])
        await msg.reply_text(f"⚠️ <b>Wait!</b> '<code>{keyword}</code>' ka filter pehle se mojood hai. Overwrite karein?", reply_markup=kb)
    else:
        await settings_col.update_one({"chat_id": chat_id}, {"$set": {f"filters.{keyword}": reply_text}}, upsert=True)
        await msg.reply_text(f"✅ Naya Filter <b>{keyword}</b> successfully add ho gaya!")

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
        
        if final_msg and final_msg != "OFF":
            try: await client.send_message(chat_id=user.id, text=final_msg)
            except: pass

# ==========================================
# 6. CHANNEL AUTO-INDEXER (SMART POST CATCHER)
# ==========================================
@bot.on_message(filters.channel & (filters.document | filters.video | filters.photo | filters.text))
async def auto_index_channel_posts(client: Client, msg: Message):
    text = msg.text or msg.caption
    if not text: return
    
    chat_id = msg.chat.id
    msg_id = msg.id
    
    first_line = text.split('\n')[0]
    clean_title = re.sub(r'[^\w\s]', ' ', first_line).lower().strip()
    clean_title = re.sub(r'\s+', ' ', clean_title)
    
    if not clean_title: return
        
    if msg.chat.username:
        link = f"https://t.me/{msg.chat.username}/{msg_id}"
    else:
        link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{msg_id}"
        
    await autofilter_col.update_one(
        {"chat_id": chat_id, "msg_id": msg_id},
        {"$set": {"title": clean_title, "raw_text": first_line, "link": link}},
        upsert=True
    )

# --- DIRECT REACTION BYPASS FUNCTION ---
async def send_reaction_direct(chat_id, message_id, emoji):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMessageReaction"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reaction": [{"type": "emoji", "emoji": emoji}]
    }
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, json=payload)
    except:
        pass

# --- GROUP CHAT LISTENER (MANUAL + AUTO-INDEX SEARCH) ---
@bot.on_message(filters.group & filters.text & ~filters.command([]))
async def group_filter_handler(client: Client, msg: Message):
    if msg.text.startswith('/'): return
    chat_id = str(msg.chat.id)
    chat_data = await get_chat_data(chat_id)
    user_query = msg.text.lower().strip()
    
    # 1. PEHLE MANUAL FILTERS CHECK KAREGA
    for kw, reply in chat_data.get('filters', {}).items():
        pattern = r'\b' + re.escape(kw.lower()) + r'\b'
        if re.search(pattern, user_query):
            emoji_list = ["🔥", "❤️", "👍", "🎉", "🍿", "💯", "🚀", "😍", "👏"]
            await send_reaction_direct(msg.chat.id, msg.id, random.choice(emoji_list))
            
            sent = await msg.reply_text(f"<b>{reply}</b>", disable_web_page_preview=True)
            new_cleanup = chat_data.get('cleanup', []) + [{"chat_id": sent.chat.id, "message_id": sent.id, "delete_at": time.time() + 86400}]
            await update_chat_data(chat_id, {"cleanup": new_cleanup})
            return 

            # 2. AGAR MANUAL MEIN NAHI MILA, TOH AUTO-CHANNEL DATABASE MEIN DHUNDHEGA
    if len(user_query) > 2:
        # Brackets/symbols hatane wala FIX
        clean_query = re.sub(r'[^\w\s]', ' ', user_query).lower().strip()
        clean_query = re.sub(r'\s+', ' ', clean_query)
        
        # Step 1: Pehle EXACT Match try karega
        search_pattern = r'\b' + re.escape(clean_query) + r'\b'
        results = await autofilter_col.find({"title": {"$regex": search_pattern, "$options": "i"}}).to_list(length=1)
        
        # 👇 NAYA VIP SMART SEARCH FIX 👇
        # Step 2: Agar EXACT match fail ho jaye (user ne extra word likhe hon), 
        # toh peeche se 1-1 word kam karke dhoondhega (Jaise: "you are desire hind k drama" -> "you are desire")
        if not results:
            words = clean_query.split()
            for i in range(len(words) - 1, 1, -1):
                short_query = " ".join(words[:i])
                short_pattern = r'\b' + re.escape(short_query) + r'\b'
                results = await autofilter_col.find({"title": {"$regex": short_pattern, "$options": "i"}}).to_list(length=1)
                if results:
                    break # Movie milte hi search rok dega
        # 👆 SMART SEARCH KHATAM 👆
        
        if results:
            res = results[0]
            emoji_list = ["🔥", "❤️", "👍", "🎉", "🍿", "💯", "🚀", "😍", "👏"]
            await send_reaction_direct(msg.chat.id, msg.id, random.choice(emoji_list))
            
            reply_text = f"🎬 <b>{res['raw_text']}</b>\n\n👉 <a href='{res['link']}'>𝗖𝗟𝗜𝗖𝗞 𝗛𝗘𝗥𝗘 𝗧𝗢 𝗚𝗘𝗧 𝗟𝗜𝗡𝗞𝗦</a>"
            sent = await msg.reply_text(reply_text, disable_web_page_preview=True)
            
            new_cleanup = chat_data.get('cleanup', []) + [{"chat_id": sent.chat.id, "message_id": sent.id, "delete_at": time.time() + 86400}]
            await update_chat_data(chat_id, {"cleanup": new_cleanup})
            return
            
        
        if results:
            res = results[0]
            emoji_list = ["🔥", "❤️", "👍", "🎉", "🍿", "💯", "🚀", "😍", "👏"]
            await send_reaction_direct(msg.chat.id, msg.id, random.choice(emoji_list))
            
            reply_text = f"🎬 <b>{res['raw_text']}</b>\n\n👉 <a href='{res['link']}'>𝗖𝗟𝗜𝗖𝗞 𝗛𝗘𝗥𝗘 𝗧𝗢 𝗚𝗘𝗧 𝗟𝗜𝗡𝗞𝗦</a>"
            sent = await msg.reply_text(reply_text, disable_web_page_preview=True)
            
            new_cleanup = chat_data.get('cleanup', []) + [{"chat_id": sent.chat.id, "message_id": sent.id, "delete_at": time.time() + 86400}]
            await update_chat_data(chat_id, {"cleanup": new_cleanup})
            return

# --- X-RAY COMMAND (DATABASE CHECKER - ALL USERS) ---
@bot.on_message(filters.command("listdb") & filters.group)
async def cmd_listdb(client: Client, msg: Message):
    wait_msg = await msg.reply_text("🔍 <i>Database check kar raha hu...</i>")
    docs = await autofilter_col.find({}).sort("_id", -1).limit(10).to_list(length=10)
    
    if not docs:
        return await wait_msg.edit_text("📭 <b>Database is Empty!</b>\nBot ne channel se koi post save NAHI ki hai.")
        
    text = "📁 <b>Bot Ke Dimaag Me Save Hue Last 10 Posts:</b>\n\n"
    for d in docs:
        text += f"• <code>{d.get('title')}</code>\n"
    text += "\n<i>👉 Aapko exact yahi naam copy karke group me bhejna hoga tabhi bot link dega!</i>"
    await wait_msg.edit_text(text)

# --- BACKGROUND DYNAMIC CLEANUP TASK (EDIT FILTERS / DELETE BATCHES) ---
async def cleanup_task():
    while True:
        await asyncio.sleep(15) 
        async for chat in settings_col.find({"cleanup": {"$not": {"$size": 0}}}):
            valid = []
            for item in chat.get('cleanup', []):
                if time.time() >= item['delete_at']:
                    try:
                        if item.get("action") == "delete":
                            await bot.delete_messages(
                                chat_id=item['chat_id'], 
                                message_ids=item['message_id']
                            )
                        else:
                            await bot.edit_message_text(
                                chat_id=item['chat_id'], 
                                message_id=item['message_id'],
                                text="<b>💖 ᴊᴜꜱᴛ ꜱᴇɴᴅ ᴛʜᴇ ᴛɪᴛʟＥ, ᴀɴᴅ ɪ'ʟʟ ɢᴇᴛ ɪᴛ ꜰᴏʀ ʏᴏᴜ ɪɴꜱᴛᴀɴᴛʟʏ! 👇</b>"
                            )
                    except: pass
                else: 
                    valid.append(item)
            await update_chat_data(chat['chat_id'], {"cleanup": valid})

# --- RENDER WEB ALIVE SERVER ---
async def handle_ping(request): return web.Response(text="Pyrogram VIP Bot Active!")
async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

# --- MAIN ENGINE RUN ---
async def start_bot():
    if not BOT_TOKEN or not MONGO_URI or API_ID == '0' or not API_HASH:
        logging.error("❌ CRASH PREVENTED: Please add API_ID, API_HASH, BOT_TOKEN, and MONGO_URI in Render Environment Variables!")
        return
        
    await start_dummy_server()
    asyncio.create_task(cleanup_task())
    
    await bot.start()
    logging.info("🚀 Pyrogram VIP Bot is Now Online & Running Perfectly!")
    await idle()
    await bot.stop()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_bot())
