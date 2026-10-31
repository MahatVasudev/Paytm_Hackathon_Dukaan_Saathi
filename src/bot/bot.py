import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update  
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler, CommandHandler,  
                          ContextTypes, MessageHandler, filters)

import src.bot.docai  
import src.bot.stt  # Make sure your STT file is named stt.py
from src.bot.llm import get  

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MAX_VOICE_SECONDS = 30            
MAX_FILE_BYTES = 20 * 1024 * 1024  
DOC_TYPES = {"application/pdf", "image/jpeg", "image/png"}

# The confirmation keyboard
CONFIRM_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ Yes, correct", callback_data="confirm:yes"),
    InlineKeyboardButton("✏️ Edit", callback_data="confirm:edit"),
]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I am Dukaan Saathi. Send me a text message, a voice note 🎙, or a bill 📄."
    )

# =============================================================================
# VOICE HANDLER (STT) -> Asks for confirmation
# =============================================================================
async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    voice = msg.voice

    if voice.duration > MAX_VOICE_SECONDS:
        await msg.reply_text(f"That voice note is {voice.duration}s long. Please keep it under {MAX_VOICE_SECONDS} seconds.")
        return

    await msg.chat.send_action("typing")
    tg_file = await voice.get_file()
    audio = bytes(await tg_file.download_as_bytearray())

    try:
        transcript = await asyncio.to_thread(stt.transcribe, audio)   
    except Exception:
        logging.exception("Sarvam transcription failed")
        await msg.reply_text("Sorry, I couldn't process that voice note. Please try again.")
        return

    if not transcript:
        await msg.reply_text("I couldn't hear any words in that. Please try again.")
        return

    # Triggers the "Is this correct?" message
    await ask_to_confirm(msg, context, transcript, heading="🎙 I heard:")

# =============================================================================
# DOCUMENT HANDLER (DocAI) -> Asks for confirmation
# =============================================================================
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.photo:
        media, filename, ctype = msg.photo[-1], "photo.jpg", "image/jpeg"
    else:
        media = msg.document
        filename, ctype = media.file_name or "document", media.mime_type or ""

    if ctype not in DOC_TYPES:
        await msg.reply_text("Please send a PDF, JPG or PNG.")
        return
    if media.file_size and media.file_size > MAX_FILE_BYTES:
        await msg.reply_text("That file is too large (limit 20 MB).")
        return

    await msg.reply_text("📄 Reading your document… this can take up to a minute.")
    await msg.chat.send_action("typing")
    tg_file = await media.get_file()
    data = bytes(await tg_file.download_as_bytearray())

    try:
        extracted = await asyncio.to_thread(docai.extract, data, filename, ctype)  
    except docai.DocAIError as e:
        await msg.reply_text(str(e))
        return
    except Exception:
        logging.exception("Sarvam document extraction failed")
        await msg.reply_text("Sorry, I couldn't process that document. Please try again.")
        return

    # Triggers the "Is this correct?" message
    await ask_to_confirm(msg, context, docai.format_result(extracted), heading="📄 I read this from your document:")

# =============================================================================
# CONFIRMATION LOGIC -> Sends to LLM after "Yes"
# =============================================================================
async def ask_to_confirm(msg, context: ContextTypes.DEFAULT_TYPE, text: str, heading: str):
    context.user_data["pending_text"] = text
    context.user_data.pop("editing", None)
    await msg.reply_text(f"{heading}\n\n{text}\n\nIs this correct?", reply_markup=CONFIRM_KB)

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text = context.user_data.get("pending_text")

    if not text:
        await q.edit_message_text("This has expired. Please send it again.")
        return

    if q.data == "confirm:yes":
        context.user_data.pop("pending_text", None)
        await q.edit_message_text(f"✅ Confirmed. Testing connection to LLM...\n\n{text}")
        
        # Calls the LLM script upon confirmation
        try:
            llm_response = await get_execution_plan(text)
            await q.message.reply_text(f"🧠 LLM JSON Output:\n```json\n{llm_response}\n```", parse_mode="Markdown")
        except Exception as e:
            logging.exception("LLM generation failed")
            await q.message.reply_text(f"❌ Error getting LLM response: {e}")
            
    elif q.data == "confirm:edit":
        context.user_data["editing"] = True
        await q.edit_message_text(f"✏️ Current text:\n\n{text}\n\n"
                                  "Please type the corrected text and send it.")

# =============================================================================
# PLAIN TEXT HANDLER -> Sends directly to LLM
# =============================================================================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    
    if context.user_data.get("editing"):
        # Handles user typing edits for Voice/Doc flows
        await ask_to_confirm(update.message, context, user_text, heading="✏️ Updated text:")
    else:
        # Handles plain text directly to the LLM without confirmation
        await update.message.reply_text("⏳ Processing text directly through LLM...")
        try:
            llm_response = await get_execution_plan(user_text)
            await update.message.reply_text(f"🧠 LLM JSON Output:\n```json\n{llm_response}\n```", parse_mode="Markdown")
        except Exception as e:
            logging.exception("LLM generation failed")
            await update.message.reply_text(f"❌ Error getting LLM response: {e}")




def main():
    app = ApplicationBuilder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))                       
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.PDF | filters.Document.IMAGE, on_document))
    app.add_handler(CallbackQueryHandler(on_button))
    
    # Crucial for direct text functionality: ensures standard messages are routed to on_text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("Bot is running... press Ctrl+C to stop")
    app.run_polling()

if __name__ == "__main__":
    main()
