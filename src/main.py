import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from sqlalchemy import select, insert
from sqlalchemy.ext.asyncio import create_async_engine

from pathlib import Path

from src.bot import docai, stt
from src.bot.reminders import run_scheduler
from src.server.templates.output import esc, render_template, select_and_render
from src.server.database.ops.utils import ExecutionContext
from src.server.database.sqltypes import businesses
from src.bot.llm import DukaanOrchestrator  # adjust path to your orchestrator file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MAX_VOICE_SECONDS = 30
MAX_FILE_BYTES = 20 * 1024 * 1024
DOC_TYPES = {"application/pdf", "image/jpeg", "image/png"}
IST = timezone(timedelta(hours=5, minutes=30))

# -----------------------------------------------------------------------------
# 1. Database Connection & Orchestrator Initialization
# -----------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/dukaansaathi")
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)

# Load schema metadata once
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema_context.json"
with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
    schema_dict = json.load(f)

orchestrator = DukaanOrchestrator(db_schema_context=schema_dict)

CONFIRM_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ Yes, correct", callback_data="confirm:yes"),
    InlineKeyboardButton("✏️ Edit", callback_data="confirm:edit"),
]])

# -----------------------------------------------------------------------------
# 2. Tenant Context Helper
# -----------------------------------------------------------------------------
async def get_or_create_business_id(conn, chat_id: str, sender_name: str) -> int:
    """Finds existing business or auto-registers a new Telegram tenant."""
    stmt = select(businesses.c.id).where(
        businesses.c.channel == "telegram",
        businesses.c.external_chat_id == str(chat_id)
    )
    result = await conn.execute(stmt)
    b_id = result.scalar_one_or_none()
    
    if b_id is None:
        ins = insert(businesses).values(
            name=sender_name or "My Dukaan",
            channel="telegram",
            external_chat_id=str(chat_id),
            language_pref="en"
        ).returning(businesses.c.id)
        res = await conn.execute(ins)
        b_id = res.scalar_one()
        await conn.commit()
        
    return b_id

# -----------------------------------------------------------------------------
# 3. Response Dispatcher / Formatter
# -----------------------------------------------------------------------------
def cb(data: str) -> str:
    """Telegram callback_data is limited to 64 BYTES (Devanagari chars are 3 bytes each)."""
    return data.encode("utf-8")[:64].decode("utf-8", errors="ignore")


def md(text) -> str:
    """Escape Telegram legacy-Markdown specials in dynamic text (unescaped _ or * makes send fail)."""
    out = str(text)
    for ch in ("\\", "_", "*", "`", "["):
        out = out.replace(ch, "\\" + ch)
    return out


def _direction(d: str | None) -> str:
    return (d or "settled").replace("_", " ")


async def dispatch_orchestrator_result(msg, result: dict, context: ContextTypes.DEFAULT_TYPE):
    """Translates orchestrator results into Telegram messages or interactive buttons."""
    res_type = result.get("type")

    if res_type == "clarification_needed":
        context.user_data["awaiting_clarification"] = True
        issue_type = result.get("issue")
        issue_data = result.get("data") or {}

        # 1. Party not found: offer to add as a new party
        if issue_type == "not_found":
            party_name = issue_data.get("queried_name") or "this contact"
            keyboard = [
                [InlineKeyboardButton(f"➕ Add '{party_name}' as new"[:60], callback_data=cb(f"clarify:new_party:{party_name}"))],
                [InlineKeyboardButton("✏️ Search different name", callback_data="clarify:retry")],
            ]
            await msg.reply_text(
                f"I couldn't find anyone named *{md(party_name)}* in your records. Add them as a new party?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )
            return

        # 2. Multi-match ambiguity
        candidates = issue_data.get("candidates", [])
        if candidates:
            keyboard = [
                [InlineKeyboardButton(
                    f"{c['name']} ({c.get('phone_last4') or 'no-phone'}) - ₹{c.get('balance', 0)}",
                    callback_data=f"clarify:party:{c['party_id']}",
                )]
                for c in candidates
            ]
            await msg.reply_text("I found multiple matches. Which one did you mean?",
                                 reply_markup=InlineKeyboardMarkup(keyboard))
            return

        # 3. Options from ask_user / missing field
        options = issue_data.get("options", [])
        if options:
            keyboard = []
            for opt in options:
                if isinstance(opt, dict):
                    opt_id = opt.get("id")
                    opt_label = opt.get("label", opt_id)
                else:
                    opt_id = opt_label = str(opt)
                keyboard.append([InlineKeyboardButton(str(opt_label), callback_data=cb(f"clarify:opt:{opt_id}"))])
            field = str(issue_data.get("field") or "").replace("_", " ")
            await msg.reply_text(f"Please choose{' ' + field if field else ''}:",
                                 reply_markup=InlineKeyboardMarkup(keyboard))
            return

        # 4. Free-text question
        if issue_data.get("message"):
            await msg.reply_text(str(issue_data["message"]))
            return
        field = str(issue_data.get("field") or issue_type).replace("_", " ")
        await msg.reply_text(f"Could you please clarify: {field}?")

    elif res_type == "multi":
        context.user_data.pop("awaiting_clarification", None)
        parts = [t for r in result["results"] if (t := select_and_render(r))]
        if parts:
            await msg.reply_text("\n\n".join(parts), parse_mode="Markdown")
        else:
            await msg.reply_text("No records found.")

    elif res_type == "party_not_found":
        context.user_data.pop("awaiting_clarification", None)
        await msg.reply_text(render_template("party_not_found", {"queried_name": esc(result.get("queried_name", ""))}),
                             parse_mode="Markdown")

    elif res_type in ("table_view", "template") and (text := select_and_render(result)):
        context.user_data.pop("awaiting_clarification", None)
        data = result.get("data") or {}
        kb = None
        if "action_id" in data:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Save", callback_data=f"pa:confirm:{data['action_id']}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"pa:cancel:{data['action_id']}"),
            ]])
        await msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)

    elif res_type == "table_view":
        context.user_data.pop("awaiting_clarification", None)
        rows = (result.get("data") or {}).get("rows", [])
        if not rows:
            await msg.reply_text("No records found for that period.")
            return

        lines = ["📊 *Results:*"]
        for r in rows:
            if "dim_party" in r or "value" in r:
                dims = " | ".join(f"{k[4:]}: {v}" for k, v in r.items() if k.startswith("dim_"))
                lines.append(f"• {md(dims + ' → ' if dims else '')}*{md(r.get('value'))}*")
            elif "total" in r:
                lines.append(f"• {md(r.get('date', ''))} | {md(str(r.get('type', '')).upper())} | ₹{md(r.get('total'))} ({md(r.get('note') or '-')})")
            elif "balance" in r:
                lines.append(f"• *{md(r.get('name'))}*: ₹{md(r.get('balance'))} ({md(_direction(r.get('direction')))})")
            else:
                lines.append(f"• {md(json.dumps(r, default=str))}")
        await msg.reply_text("\n".join(lines), parse_mode="Markdown")

    elif res_type == "template":
        context.user_data.pop("awaiting_clarification", None)
        data = result.get("data") or {}
        op = result.get("op", "")

        if "action_id" in data:
            action_desc = op.replace("propose_", "").replace("_", " ").capitalize()
            warning = f"\n⚠️ _{md(data['warning'])}_" if data.get("warning") else ""
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Save", callback_data=f"pa:confirm:{data['action_id']}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"pa:cancel:{data['action_id']}"),
            ]])
            await msg.reply_text(
                f"📝 *{md(action_desc)} ready*{warning}\n\nSave this entry?",
                parse_mode="Markdown", reply_markup=kb,
            )
        elif "balance" in data:
            await msg.reply_text(f"💰 Balance: *₹{md(data['balance'])}* ({md(_direction(data.get('direction')))})",
                                 parse_mode="Markdown")
        elif "candidates" in data and len(data["candidates"]) == 1:
            c = data["candidates"][0]
            await msg.reply_text(f"👤 {c['name']}: ₹{c['balance']} ({_direction(c.get('direction'))})")
        elif data.get("message") and len(data) == 1:
            await msg.reply_text("No records found." if data["message"] in ("no_activity", "none_found", "none", "no_results")
                                 else str(data["message"]))
        else:
            await msg.reply_text(f"✅ Done: {json.dumps(data, default=str)}")

    elif res_type == "committed":
        context.user_data.pop("awaiting_clarification", None)
        d = result.get("data") or {}
        if d.get("voided"):
            text = f"🗑 Entry #{d['transaction_id']} voided."
        elif d.get("corrected"):
            text = f"✏️ Entry #{d['transaction_id']} corrected."
        else:
            label = str(d.get("type", "entry")).replace("_", " ").capitalize()
            text = f"✅ {label} saved: ₹{d.get('total')} (paid ₹{d.get('paid')})"
            if d.get("direction"):
                text += f"\n💰 Balance now: ₹{d.get('balance')} ({_direction(d.get('direction'))})"
        await msg.reply_text(text)

    elif res_type == "cancelled":
        context.user_data.pop("awaiting_clarification", None)
        await msg.reply_text("❌ Cancelled. Nothing was saved.")

    elif res_type == "text":
        await msg.reply_text(result.get("message", "Done."))

    elif res_type == "error":
        context.user_data.pop("awaiting_clarification", None)
        await msg.reply_text(f"❌ {result.get('message', 'Something went wrong.')}")

# -----------------------------------------------------------------------------
# 4. Command & Flow Handlers
# -----------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hi! I am Dukaan Saathi. Send me a text message, a voice note 🎙, or a bill 📄.")

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

    await ask_to_confirm(msg, context, transcript, heading="🎙 I heard:")

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

    await ask_to_confirm(msg, context, docai.format_result(extracted), heading="📄 I read this from your document:")

async def ask_to_confirm(msg, context: ContextTypes.DEFAULT_TYPE, text: str, heading: str):
    context.user_data["pending_text"] = text
    context.user_data.pop("editing", None)
    await msg.reply_text(f"{heading}\n\n{text}\n\nIs this correct?", reply_markup=CONFIRM_KB)

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # Flowchart: Handling user selection of clarification options (buttons)
    if q.data.startswith("clarify:"):
        choice = q.data[len("clarify:"):]   # keep the "party:" / "new_party:" / "opt:" prefix
        if choice == "retry":
            context.user_data["awaiting_clarification"] = True
            await q.edit_message_text("✏️ Please type the correct name (or send your request again).")
            return
        await q.edit_message_text(f"Selected: {choice.split(':', 1)[-1]}")

        async with engine.connect() as conn:
            business_id = await get_or_create_business_id(conn, q.message.chat_id, q.from_user.full_name)
            ctx = ExecutionContext(conn=conn, business_id=business_id, now_ist=datetime.now(IST))
            
            # Pass as clarification reply back to orchestrator state machine
            res = await orchestrator.handle_message(choice, ctx, is_clarification_reply=True)
            await conn.commit()
            await dispatch_orchestrator_result(q.message, res, context)
        return

    # Save / Cancel buttons on a staged entry
    if q.data.startswith("pa:"):
        _, action, action_id = q.data.split(":", 2)
        await q.edit_message_reply_markup(reply_markup=None)
        async with engine.connect() as conn:
            business_id = await get_or_create_business_id(conn, q.message.chat_id, q.from_user.full_name)
            ctx = ExecutionContext(conn=conn, business_id=business_id, now_ist=datetime.now(IST))
            res = await orchestrator.confirm_pending(ctx, int(action_id), cancel=(action == "cancel"))
            await conn.commit()
        await dispatch_orchestrator_result(q.message, res, context)
        return

    text = context.user_data.get("pending_text")
    if not text:
        await q.edit_message_text("This has expired. Please send it again.")
        return

    if q.data == "confirm:yes":
        context.user_data.pop("pending_text", None)
        await q.edit_message_text(f"✅ Confirmed.\n\n_{text}_", parse_mode="Markdown")
        await q.message.chat.send_action("typing")

        async with engine.connect() as conn:
            business_id = await get_or_create_business_id(conn, q.message.chat_id, q.from_user.full_name)
            ctx = ExecutionContext(conn=conn, business_id=business_id, now_ist=datetime.now(IST))
            
            res = await orchestrator.handle_message(text, ctx, is_clarification_reply=False)
            await conn.commit()
            await dispatch_orchestrator_result(q.message, res, context)

    elif q.data == "confirm:edit":
        context.user_data["editing"] = True
        await q.edit_message_text(f"✏️ Current text:\n\n{text}\n\nPlease type the corrected text and send it.")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()

    if context.user_data.get("editing"):
        await ask_to_confirm(update.message, context, user_text, heading="✏️ Updated text:")
        return

    # Check if this text message is answering an active ambiguity/clarification
    is_clarification = context.user_data.get("awaiting_clarification", False)
    await update.message.chat.send_action("typing")

    async with engine.connect() as conn:
        business_id = await get_or_create_business_id(conn, update.effective_chat.id, update.effective_user.full_name)
        ctx = ExecutionContext(conn=conn, business_id=business_id, now_ist=datetime.now(IST))
        
        res = await orchestrator.handle_message(
            user_text=user_text,
            ctx=ctx,
            is_clarification_reply=is_clarification
        )
        await conn.commit()
        await dispatch_orchestrator_result(update.message, res, context)

# -----------------------------------------------------------------------------
# 5. Application Loop
# -----------------------------------------------------------------------------
def main():
    async def post_init(application):
        # Reminder sender runs alongside the bot; plain templates, no LLM
        application.bot_data["reminder_task"] = asyncio.create_task(run_scheduler(engine, application.bot))

    async def post_shutdown(application):
        task = application.bot_data.get("reminder_task")
        if task:
            task.cancel()

    app = (ApplicationBuilder().token(os.environ["TELEGRAM_BOT_TOKEN"])
           .post_init(post_init).post_shutdown(post_shutdown).build())

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.PDF | filters.Document.IMAGE, on_document))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("Bot is running... press Ctrl+C to stop")
    app.run_polling()

if __name__ == "__main__":
    main()
