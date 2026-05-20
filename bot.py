import os
import sqlite3
import time
import logging
from datetime import datetime
from dotenv import load_dotenv
import telebot
from telebot import types
import requests

load_dotenv()

# ---------- CONFIG FROM ENV ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

bot = telebot.TeleBot(TELEGRAM_TOKEN)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------- DATABASE ----------
DB_FILE = "lionwriter.db"

def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    # Users table (added chat_mode column)
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, style TEXT DEFAULT 'default',
                  daily_requests INTEGER DEFAULT 0, last_request_date TEXT,
                  chat_mode INTEGER DEFAULT 0)''')
    # Content generation history
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, prompt TEXT,
                  mode TEXT, response TEXT, timestamp TEXT)''')
    # Chat messages (persistent memory)
    c.execute('''CREATE TABLE IF NOT EXISTS chat_messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT,
                  content TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()

init_db()

# ---------- STYLES ----------
SYSTEM_PROMPT = """
You are Lion's professional content writer (@pin_lion from Dhaka).
Style: Energetic, motivational, simple but powerful English, heavy emojis, strong Web3 & crypto alpha vibe with Bangladesh touch.
Return only the final ready-to-copy content. No explanations.
"""

STYLES = {
    "default": SYSTEM_PROMPT,
    "formal": "You are a professional business writer. Use formal, polished English. No emojis.",
    "sarcastic": "You are a witty, sarcastic content creator. Use sharp humour, irony, and light sarcasm.",
    "banglish": "Mix Bengali and English naturally. Use Banglish (Bengali in Latin script). Energetic, fun."
}

# System prompt used when in chat mode (the bot is a helpful assistant)
CHAT_SYSTEM_PROMPT = """
You are Lion's AI assistant, a friendly and knowledgeable helper from Bangladesh.
You can discuss anything, answer questions, and give advice.
Keep responses helpful, concise, and warm. Use emojis where appropriate.
"""

# ---------- DATABASE HELPERS ----------
def get_db():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def ensure_user(user_id, username=None):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

def set_user_style(user_id, style):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET style=? WHERE user_id=?", (style, user_id))
    conn.commit()
    conn.close()

def get_user_style(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT style FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else "default"

def toggle_chat_mode(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT chat_mode FROM users WHERE user_id=?", (user_id,))
    current = c.fetchone()
    if current is None:
        c.execute("INSERT INTO users (user_id, chat_mode) VALUES (?, 1)", (user_id,))
        new_mode = 1
    else:
        new_mode = 0 if current[0] else 1
        c.execute("UPDATE users SET chat_mode=? WHERE user_id=?", (new_mode, user_id))
    conn.commit()
    conn.close()
    return new_mode

def is_chat_mode(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT chat_mode FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return bool(row[0]) if row else False

def check_and_update_limit(user_id):
    conn = get_db()
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT daily_requests, last_request_date FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if row is None:
        c.execute("INSERT INTO users (user_id, daily_requests, last_request_date) VALUES (?, 1, ?)", (user_id, today))
        conn.commit()
        conn.close()
        return True
    reqs, last_date = row
    if last_date != today:
        c.execute("UPDATE users SET daily_requests=1, last_request_date=? WHERE user_id=?", (today, user_id))
        conn.commit()
        conn.close()
        return True
    if reqs >= 20:
        conn.close()
        return False
    c.execute("UPDATE users SET daily_requests=daily_requests+1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    return True

def log_request(user_id, prompt, mode, response):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO history (user_id, prompt, mode, response, timestamp) VALUES (?,?,?,?,?)",
              (user_id, prompt, mode, response, datetime.now().isoformat()))
    conn.commit()
    conn.close()

# ---------- CHAT MEMORY ----------
MAX_CHAT_MESSAGES = 20   # Keep last 20 messages for context

def add_chat_message(user_id, role, content):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO chat_messages (user_id, role, content, timestamp) VALUES (?,?,?,?)",
              (user_id, role, content, datetime.now().isoformat()))
    # Remove older messages if exceeding limit
    c.execute("""DELETE FROM chat_messages WHERE id IN (
                   SELECT id FROM chat_messages WHERE user_id=? ORDER BY id ASC LIMIT (
                     SELECT MAX(0, COUNT(*) - ?) FROM chat_messages WHERE user_id=?
                   ))""", (user_id, MAX_CHAT_MESSAGES, user_id, MAX_CHAT_MESSAGES))
    conn.commit()
    conn.close()

def get_chat_context(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT role, content FROM chat_messages WHERE user_id=? ORDER BY id ASC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{"role": r, "content": c} for r, c in rows]

def clear_chat_history(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM chat_messages WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

# ---------- DEEPSEEK API ----------
def call_deepseek(messages, temperature=0.85, max_tokens=1500):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    resp = requests.post("https://api.deepseek.com/v1/chat/completions",
                         json=payload, headers=headers, timeout=80)
    if resp.status_code == 200:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    else:
        raise Exception(f"DeepSeek API error {resp.status_code}: {resp.text[:200]}")

# ---------- KEYBOARDS ----------
def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('📝 Single Tweet', '🧵 Thread (4-6 tweets)')
    markup.add('📢 Telegram Post', '🔥 Viral Caption')
    markup.add('💼 LinkedIn Post', '📧 Email Draft')
    markup.add('💬 Chat Mode', '🎨 Change Style')
    markup.add('ℹ️ Help')
    return markup

def regen_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔄 Regenerate", callback_data="regen"))
    return markup

# ---------- COMMANDS ----------
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    ensure_user(user_id, message.from_user.username)
    set_user_style(user_id, "default")
    bot.reply_to(message,
        "🦁 *LionWriter Pro* is online!\n"
        "Write anything or use the buttons.\n\n"
        "💬 Use the *Chat Mode* button for normal conversation.\n"
        "/style - change writing personality\n"
        "/stats - your usage\n"
        "/clear - clear chat memory",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard())

@bot.message_handler(commands=['style'])
def change_style_cmd(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    for key in STYLES:
        markup.add(types.InlineKeyboardButton(f"✨ {key.capitalize()}", callback_data=f"style_{key}"))
    bot.reply_to(message, "Choose your writing personality:", reply_markup=markup)

@bot.message_handler(commands=['help'])
def help_cmd(message):
    bot.reply_to(message,
        "📘 *LionWriter Commands*\n"
        "/start - Main menu\n"
        "/style - Change writing style\n"
        "/stats - Your usage (admin: global stats)\n"
        "/clear - Erase your chat memory\n"
        "Buttons: generate content, switch to chat mode, etc.\n\n"
        "In *Chat Mode*, just type naturally and I'll reply.\n"
        "In *Content Mode*, I turn ideas into ready‑to‑post text.",
        parse_mode="Markdown")

@bot.message_handler(commands=['clear'])
def clear_chat_cmd(message):
    clear_chat_history(message.from_user.id)
    bot.reply_to(message, "🧹 Chat memory cleared.")

# ---------- ADMIN ----------
@bot.message_handler(commands=['stats'])
def stats(message):
    user_id = message.from_user.id
    conn = get_db()
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
        c.execute("SELECT daily_requests, last_request_date FROM users WHERE user_id=?", (user_id,))
        row = c.fetchone()
        conn.close()
        if row:
            reqs, last = row
            bot.reply_to(message, f"📊 *Your Stats*\n📝 Requests today: {reqs}/20\n📅 Last active: {last}", parse_mode="Markdown")
        else:
            bot.reply_to(message, "No data yet.")

@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if message.from_user.id != ADMIN_USER_ID:
        return
    text = message.text.replace('/broadcast', '').strip()
    if not text:
        bot.reply_to(message, "Usage: /broadcast <message>")
        return
    conn = get_db()
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

# ---------- CALLBACKS ----------
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
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT prompt, mode FROM history WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        bot.answer_callback_query(call.id, "No previous generation to regenerate.")
        return
    prompt_text, mode = row
    bot.answer_callback_query(call.id, "Regenerating...")
    if not check_and_update_limit(user_id):
        bot.send_message(call.message.chat.id, "⚠️ Daily limit reached.")
        return
    style_key = get_user_style(user_id)
    system_prompt = STYLES.get(style_key, SYSTEM_PROMPT)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Mode: {mode}\nIdea: {prompt_text}"}
    ]
    try:
        content = call_deepseek(messages)
        log_request(user_id, prompt_text, mode, content)
        bot.send_message(call.message.chat.id,
            f"✅ *Regenerated {mode}*\n\n{content}\n\n_Copy & post!_",
            parse_mode="Markdown", reply_markup=regen_keyboard())
    except Exception as e:
        bot.send_message(call.message.chat.id, f"⚠️ Regeneration failed: {str(e)[:100]}")

# ---------- MAIN MESSAGE HANDLER ----------
@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_message(message):
    user_id = message.from_user.id
    text = message.text.strip()
    ensure_user(user_id, message.from_user.username)

    # Toggle chat mode button
    if text == '💬 Chat Mode':
        new_state = toggle_chat_mode(user_id)
        if new_state:
            bot.reply_to(message, "💬 *Chat Mode ON*. I'm now your AI assistant. Just talk to me! Send a content button to generate posts.", parse_mode="Markdown")
        else:
            bot.reply_to(message, "📝 *Content Mode ON*. You can use the buttons or send an idea to create content.", parse_mode="Markdown")
        return

    # Content generation buttons
    content_modes = {
        '📝 Single Tweet': 'Single Tweet',
        '🧵 Thread (4-6 tweets)': 'Thread',
        '📢 Telegram Post': 'Telegram Post',
        '🔥 Viral Caption': 'Viral Caption',
        '💼 LinkedIn Post': 'LinkedIn Post',
        '📧 Email Draft': 'Email Draft'
    }
    if text in content_modes:
        mode = content_modes[text]
        # Prompt user for idea
        bot.reply_to(message, f"✏️ Send me the idea/rough text for your {mode}.")
        # Save mode temporarily? We'll use a simple state: just wait for next message.
        # For simplicity, we'll handle the next message as the idea for that mode.
        # Register a next step handler
        msg = bot.send_message(message.chat.id, "Waiting for your input...")
        bot.register_next_step_handler(msg, process_content_idea, mode)
        return

    # Help button
    if text == '🎨 Change Style':
        change_style_cmd(message)
        return
    if text == 'ℹ️ Help':
        help_cmd(message)
        return

    # ---------- CHAT MODE vs CONTENT MODE ----------
    if is_chat_mode(user_id):
        # Chat mode: conversation with memory
        if not check_and_update_limit(user_id):
            bot.reply_to(message, "⚠️ Daily limit reached.")
            return
        # Save user message
        add_chat_message(user_id, "user", text)
        # Build context
        context = get_chat_context(user_id)
        messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}] + context
        try:
            reply = call_deepseek(messages, temperature=0.9, max_tokens=1000)
            add_chat_message(user_id, "assistant", reply)
            bot.reply_to(message, reply, parse_mode="Markdown")
        except Exception as e:
            bot.reply_to(message, f"⚠️ Chat error: {str(e)[:150]}")
    else:
        # Default: Content generation from free text
        if not check_and_update_limit(user_id):
            bot.reply_to(message, "⚠️ Daily limit reached.")
            return
        style_key = get_user_style(user_id)
        system_prompt = STYLES.get(style_key, SYSTEM_PROMPT)
        # In content mode, any free text is treated as "Single Tweet" idea
        mode = "Single Tweet"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Mode: {mode}\nIdea: {text}"}
        ]
        wait_msg = bot.reply_to(message, "🧠 Writing...")
        try:
            content = call_deepseek(messages)
            log_request(user_id, text, mode, content)
            bot.edit_message_text(
                f"✅ *{mode} Ready*\n\n{content}\n\n_Copy & post!_",
                chat_id=message.chat.id, message_id=wait_msg.message_id,
                parse_mode="Markdown", reply_markup=regen_keyboard()
            )
        except Exception as e:
            bot.edit_message_text(f"⚠️ Failed: {str(e)[:150]}", chat_id=message.chat.id, message_id=wait_msg.message_id)

def process_content_idea(message, mode):
    """Handle the idea sent after a content button is pressed."""
    user_id = message.from_user.id
    idea = message.text.strip()
    if not idea:
        bot.reply_to(message, "❌ No input received. Try again.")
        return
    if not check_and_update_limit(user_id):
        bot.reply_to(message, "⚠️ Daily limit reached.")
        return
    style_key = get_user_style(user_id)
    system_prompt = STYLES.get(style_key, SYSTEM_PROMPT)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Mode: {mode}\nIdea: {idea}"}
    ]
    wait_msg = bot.reply_to(message, "🧠 Writing...")
    try:
        content = call_deepseek(messages)
        log_request(user_id, idea, mode, content)
        bot.edit_message_text(
            f"✅ *{mode} Ready*\n\n{content}\n\n_Copy & post!_",
            chat_id=message.chat.id, message_id=wait_msg.message_id,
            parse_mode="Markdown", reply_markup=regen_keyboard()
        )
    except Exception as e:
        bot.edit_message_text(f"⚠️ Failed: {str(e)[:150]}", chat_id=message.chat.id, message_id=wait_msg.message_id)

# ---------- INLINE QUERY (same as before, uses DeepSeek) ----------
@bot.inline_handler(lambda query: len(query.query) > 3)
def inline_query(query):
    try:
        prompt = query.query
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Mode: Single Tweet\nIdea: {prompt}"}
        ]
        content = call_deepseek(messages, temperature=0.85, max_tokens=500)
        r = types.InlineQueryResultArticle(
            id='1', title='Generate Tweet',
            input_message_content=types.InputTextMessageContent(content)
        )
        bot.answer_inline_query(query.id, [r])
    except:
        pass

# ---------- START BOT ----------
if __name__ == "__main__":
    logger.info("🦁 LionWriter Pro with DeepSeek + Chat starting...")
    bot.remove_webhook()  # Important to avoid 409 Conflict
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(10)
