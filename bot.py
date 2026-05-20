import os
import sqlite3
import time
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import telebot
from telebot import types
import requests

load_dotenv()

# --- Config from environment ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
XAI_API_KEY = os.getenv("XAI_API_KEY")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# --- Logging setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Database initialization ---
DB_FILE = "lionwriter.db"

def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, style TEXT DEFAULT 'default',
                  daily_requests INTEGER DEFAULT 0, last_request_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, prompt TEXT, mode TEXT, response TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- Default system prompt ---
SYSTEM_PROMPT = """
You are Lion's professional content writer (@pin_lion from Dhaka).
Style: Energetic, motivational, simple but powerful English, heavy emojis, strong Web3 & crypto alpha vibe with Bangladesh touch.
Return only the final ready-to-copy content. No explanations.
"""

# --- Style presets ---
STYLES = {
    "default": SYSTEM_PROMPT,
    "formal": "You are a professional business writer. Use formal, polished English. No emojis.",
    "sarcastic": "You are a witty, sarcastic content creator. Use sharp humour, irony, and light sarcasm.",
    "banglish": "Mix Bengali and English naturally. Use Banglish (Bengali in Latin script). Energetic, fun."
}

# --- Rate limiting helper ---
def check_and_update_limit(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT daily_requests, last_request_date FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row is None:
        # new user
        c.execute("INSERT INTO users (user_id, daily_requests, last_request_date) VALUES (?, 1, ?)",
                  (user_id, today))
        conn.commit()
        conn.close()
        return True
    else:
        reqs, last_date = row
        if last_date != today:
            # reset daily count
            c.execute("UPDATE users SET daily_requests=1, last_request_date=? WHERE user_id=?", (today, user_id))
            conn.commit()
            conn.close()
            return True
        else:
            if reqs >= 20:  # free tier limit per day
                conn.close()
                return False
            else:
                c.execute("UPDATE users SET daily_requests=daily_requests+1 WHERE user_id=?", (user_id,))
                conn.commit()
                conn.close()
                return True

def log_request(user_id, prompt, mode, response):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO history (user_id, prompt, mode, response, timestamp) VALUES (?, ?, ?, ?, ?)",
              (user_id, prompt, mode, response, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_user_style(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT style FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0]
    return "default"

def set_user_style(user_id, style):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, style, daily_requests, last_request_date) VALUES (?, ?, 0, ?)",
              (user_id, style, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()

# --- Main menu ---
def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('📝 Single Tweet', '🧵 Thread (4-6 tweets)')
    markup.add('📢 Telegram Post', '🔥 Viral Caption')
    markup.add('💼 LinkedIn Post', '📧 Email Draft')
    markup.add('🎨 Change Style', 'ℹ️ Help')
    return markup

# --- Inline keyboard for regeneration ---
def regen_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔄 Regenerate", callback_data="regen"))
    return markup

# --- START command ---
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    # Auto-register
    set_user_style(user_id, "default")  # ensures row exists
    bot.reply_to(message,
        "🦁 *LionWriter Pro* is online!\n"
        "Write anything or use the buttons.\n\n"
        "Use /style to change writing personality.",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard())

# --- Style command ---
@bot.message_handler(commands=['style'])
def change_style(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    for key in STYLES:
        markup.add(types.InlineKeyboardButton(f"✨ {key.capitalize()}", callback_data=f"style_{key}"))
    bot.reply_to(message, "Choose your writing personality:", reply_markup=markup)

# --- Help ---
@bot.message_handler(commands=['help'])
def help_cmd(message):
    bot.reply_to(message,
        "📘 *LionWriter Commands*\n"
        "/start - Main menu\n"
        "/style - Change writing style\n"
        "/stats - Your usage (admin: global stats)\n"
        "Just send a topic or paste a rough idea, and I'll generate ready-to-post content.",
        parse_mode="Markdown")

# --- Admin stats ---
@bot.message_handler(commands=['stats'])
def stats(message):
    user_id = message.from_user.id
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if user_id == ADMIN_USER_ID:
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM history")
        total_gen = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM history WHERE timestamp > ?", (datetime.now().strftime("%Y-%m-%d"),))
        today_gen = c.fetchone()[0]
        conn.close()
        bot.reply_to(message, f"📊 *Global Stats*\n👥 Users: {total_users}\n📝 Total generations: {total_gen}\n🟢 Today: {today_gen}", parse_mode="Markdown")
    else:
        # User stats
        c.execute("SELECT daily_requests, last_request_date FROM users WHERE user_id=?", (user_id,))
        row = c.fetchone()
        conn.close()
        if row:
            reqs, last = row
            bot.reply_to(message, f"📊 *Your Stats*\n📝 Requests today: {reqs}/20\n📅 Last active: {last}", parse_mode="Markdown")
        else:
            bot.reply_to(message, "No data yet. Start generating!")

# --- Broadcast (admin only) ---
@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if message.from_user.id != ADMIN_USER_ID:
        return
    text = message.text.replace('/broadcast', '').strip()
    if not text:
        bot.reply_to(message, "Usage: /broadcast <message>")
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = c.fetchall()
    conn.close()
    for u in users:
        try:
            bot.send_message(u[0], f"📢 Admin message:\n{text}")
        except:
            pass
    bot.reply_to(message, f"Broadcast sent to {len(users)} users.")

# --- Core generation handler ---
@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_message(message):
    user_id = message.from_user.id
    text = message.text.strip()

    # Handle menu buttons
    if text == '🎨 Change Style':
        change_style(message)
        return
    if text == 'ℹ️ Help':
        help_cmd(message)
        return

    # Check rate limit
    if not check_and_update_limit(user_id):
        bot.reply_to(message, "⚠️ Daily limit (20 requests) reached. Try again tomorrow.")
        return

    # Determine mode
    mode_mapping = {
        '📝 Single Tweet': 'Single Tweet',
        '🧵 Thread (4-6 tweets)': 'Thread',
        '📢 Telegram Post': 'Telegram Post',
        '🔥 Viral Caption': 'Viral Caption',
        '💼 LinkedIn Post': 'LinkedIn Post',
        '📧 Email Draft': 'Email Draft'
    }
    mode = mode_mapping.get(text, 'Single Tweet')  # if not a button, treat as single tweet

    # Get user's style
    style_key = get_user_style(user_id)
    system_prompt = STYLES.get(style_key, SYSTEM_PROMPT)

    # Build prompt
    full_prompt = f"Mode: {mode}\nIdea: {text}"

    # Call xAI API
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {XAI_API_KEY}"
    }
    payload = {
        "model": "grok-4.20-reasoning",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt}
        ],
        "temperature": 0.85,
        "max_tokens": 1500
    }

    wait_msg = bot.reply_to(message, "🧠 Writing...")

    try:
        resp = requests.post("https://api.x.ai/v1/chat/completions", json=payload, headers=headers, timeout=80)
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            log_request(user_id, text, mode, content)
            bot.edit_message_text(
                f"✅ *{mode} Ready*\n\n{content}\n\n_Copy & post!_",
                chat_id=message.chat.id,
                message_id=wait_msg.message_id,
                parse_mode="Markdown",
                reply_markup=regen_keyboard()
            )
        else:
            bot.edit_message_text(f"❌ API Error {resp.status_code}", chat_id=message.chat.id, message_id=wait_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"⚠️ Failed: {str(e)[:100]}", chat_id=message.chat.id, message_id=wait_msg.message_id)

# --- Callback handlers ---
@bot.callback_query_handler(func=lambda call: call.data.startswith("style_"))
def style_callback(call):
    user_id = call.from_user.id
    style = call.data.split("_")[1]
    set_user_style(user_id, style)
    bot.answer_callback_query(call.id, f"Style set to {style.capitalize()}!")
    bot.send_message(call.message.chat.id, f"🎨 Writing style changed to *{style.capitalize()}*.", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "regen")
def regen_callback(call):
    user_id = call.from_user.id
    # Retrieve last prompt & mode from history
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT prompt, mode FROM history WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        bot.answer_callback_query(call.id, "No previous generation to regenerate.")
        return

    prompt_text, mode = row
    bot.answer_callback_query(call.id, "Regenerating...")

    # Re-check limit (regeneration counts as a new request)
    if not check_and_update_limit(user_id):
        bot.send_message(call.message.chat.id, "⚠️ Daily limit reached. Can't regenerate.")
        return

    style_key = get_user_style(user_id)
    system_prompt = STYLES.get(style_key, SYSTEM_PROMPT)

    full_prompt = f"Mode: {mode}\nIdea: {prompt_text}"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {XAI_API_KEY}"
    }
    payload = {
        "model": "grok-4.20-reasoning",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt}
        ],
        "temperature": 0.85,
        "max_tokens": 1500
    }

    try:
        resp = requests.post("https://api.x.ai/v1/chat/completions", json=payload, headers=headers, timeout=80)
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            log_request(user_id, prompt_text, mode, content)
            bot.send_message(call.message.chat.id,
                f"✅ *Regenerated {mode}*\n\n{content}\n\n_Copy & post!_",
                parse_mode="Markdown",
                reply_markup=regen_keyboard()
            )
        else:
            bot.send_message(call.message.chat.id, f"❌ Regeneration failed (API {resp.status_code})")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"⚠️ Regeneration error: {str(e)[:100]}")

# --- Inline query support (advanced) ---
@bot.inline_handler(lambda query: len(query.query) > 3)
def inline_query(query):
    try:
        prompt = query.query
        # Use default style for inline
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {XAI_API_KEY}"
        }
        payload = {
            "model": "grok-4.20-reasoning",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Mode: Single Tweet\nIdea: {prompt}"}
            ],
            "temperature": 0.85,
            "max_tokens": 500
        }
        resp = requests.post("https://api.x.ai/v1/chat/completions", json=payload, headers=headers, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            r = types.InlineQueryResultArticle(
                id='1',
                title='Generate Tweet',
                input_message_content=types.InputTextMessageContent(content)
            )
            bot.answer_inline_query(query.id, [r])
        else:
            pass
    except:
        pass

# --- Start bot with resilience ---
if __name__ == "__main__":
    logger.info("🦁 LionWriter Pro started...")
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(10)
