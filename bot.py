import telebot
import requests
import json
import os
from telebot import types

# ====================== CONFIG ======================
TELEGRAM_TOKEN = "8982499845:AAEwHKPhq4GmbwZ2nQ5lNZbGI0AfPgAQK00"
XAI_API_KEY = "xai-O3ncQZqvMSxFMa6AuhMdVcYzDAPuKdiGoGnzJD1fpcmCQTyNkmThnJIw71NvRUvJP87cJWh4NIkTNPTr"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Advanced System Prompt (Your Personal Style)
SYSTEM_PROMPT = """
You are Lion's professional content writer (@pin_lion).
Style: Energetic, motivational, simple but powerful English, heavy emojis, Dhaka/Bangladesh flavor, strong Web3 & crypto alpha vibe.
Always write ready-to-copy content. Never add "Here is your post" or explanations.
"""

# ====================== COMMANDS ======================
@bot.message_handler(commands=['start'])
def start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('📝 Single Tweet', '🧵 Thread (3-5 tweets)', '📢 Telegram Post', '🔥 Viral Caption')
    bot.reply_to(message, 
        "🦁 *LionWriter Bot Advanced* is ready!\n\n"
        "Just type your idea or use the buttons below.\n"
        "Example: `Web3 opportunities for Bangladeshis`", 
        parse_mode="Markdown", reply_markup=markup)

# ====================== MAIN HANDLER ======================
@bot.message_handler(func=lambda m: True)
def generate_content(message):
    user_text = message.text.strip()
    
    if user_text in ['📝 Single Tweet', '🧵 Thread (3-5 tweets)', '📢 Telegram Post', '🔥 Viral Caption']:
        bot.reply_to(message, f"✅ Mode selected: {user_text}\n\nNow send your topic/idea:")
        return

    # Detect mode
    mode = "Single Tweet"
    if "thread" in user_text.lower() or "🧵" in user_text:
        mode = "Thread (3-5 tweets)"
    elif "telegram" in user_text.lower() or "channel" in user_text.lower():
        mode = "Telegram Post"
    elif "caption" in user_text.lower() or "viral" in user_text.lower():
        mode = "Viral Caption"

    bot.reply_to(message, "🧠 Generating high-quality content...")

    payload = {
        "model": "grok-4.20-reasoning",
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Mode: {mode}\nTopic: {user_text}\nWrite in my style (@pin_lion)"}
        ],
        "temperature": 0.85,
        "max_tokens": 1200
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {XAI_API_KEY}"
    }

    try:
        response = requests.post("https://api.x.ai/v1/responses", 
                               json=payload, headers=headers, timeout=60)
        
        if response.status_code == 200:
            data = response.json()
            # Handle different response structures
            content = data.get("output") or data.get("content") or str(data)
            if isinstance(content, list):
                content = content[0].get("content", str(content))

            # Beautiful reply
            final_text = f"✅ **Generated in {mode}**\n\n{content}\n\n🔥 Copy & Post!"
            
            # Add buttons
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔄 Regenerate", callback_data=f"regen_{message.id}"))
            bot.reply_to(message, final_text, parse_mode="Markdown", reply_markup=markup)
            
        else:
            bot.reply_to(message, f"❌ API Error: {response.status_code}\n{response.text[:300]}")
            
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {str(e)}")

# Callback for Regenerate button
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data.startswith("regen_"):
        bot.answer_callback_query(call.id, "Regenerating...")
        # For simplicity, ask for topic again
        bot.send_message(call.message.chat.id, "Send the same topic again or a new one to regenerate.")

# ====================== RUN BOT ======================
if __name__ == "__main__":
    print("🚀 Advanced LionWriter Bot is running...")
    bot.infinity_polling()