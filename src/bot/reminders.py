"""Reminder scheduler: polls the DB and sends due reminders as plain template messages.
No LLM involved. Safe to run in several processes (rows are claimed with FOR UPDATE SKIP LOCKED)."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.server.templates.output import esc, render_template

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
POLL_SECONDS = 5
MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=2)

_CLAIM = text("""
    UPDATE reminders r
       SET status = 'processing', attempts = r.attempts + 1
     WHERE r.id IN (
            SELECT id FROM reminders
             WHERE status = 'pending' AND remind_at <= :now
             ORDER BY remind_at
             LIMIT :lim
             FOR UPDATE SKIP LOCKED)
 RETURNING r.id, r.business_id, r.party_id, r.note, r.remind_at, r.attempts
""")

_TARGET = text("""
    SELECT b.external_chat_id, b.channel, p.name AS party_name,
           COALESCE(pb.balance, 0) AS balance
      FROM businesses b
      LEFT JOIN parties p          ON p.id = :party_id AND p.business_id = b.id
      LEFT JOIN party_balances pb  ON pb.business_id = b.id AND pb.party_id = p.id
     WHERE b.id = :business_id
""")


def build_message(note: str | None, party_name: str | None, balance_paise: int) -> str:
    """Pure template rendering (the balance is looked up at fire time, never stored)."""
    note_text = esc(note or "You asked me to remind you.")
    if not party_name:
        return render_template("reminder_due", {"note": note_text})
    direction = "they owe you" if balance_paise > 0 else "you owe them" if balance_paise < 0 else "settled"
    return render_template("reminder_due_party", {
        "note": note_text, "party_name": esc(party_name),
        "balance": f"{abs(balance_paise) / 100:.2f}", "direction_text": direction,
    })


async def recover_stuck(engine: AsyncEngine) -> None:
    """After a crash, rows left in 'processing' would never be retried."""
    async with engine.begin() as conn:
        res = await conn.execute(text("UPDATE reminders SET status='pending' WHERE status='processing'"))
    if res.rowcount:
        logger.warning("Re-queued %d reminders stuck in 'processing'", res.rowcount)


async def process_due(engine: AsyncEngine, bot, now: datetime | None = None, batch: int = 20) -> int:
    """Send every reminder whose time has passed. Returns how many were sent."""
    now = now or datetime.now(IST)
    async with engine.begin() as conn:               # claim & commit before sending
        claimed = (await conn.execute(_CLAIM, {"now": now, "lim": batch})).fetchall()

    sent = 0
    for r in claimed:
        try:
            async with engine.connect() as conn:
                t = (await conn.execute(_TARGET, {"business_id": r.business_id, "party_id": r.party_id})).fetchone()
            if not t or t.channel != "telegram":
                raise RuntimeError("no telegram chat for this business")
            msg = build_message(r.note, t.party_name, int(t.balance))
            await bot.send_message(chat_id=int(t.external_chat_id), text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.exception("Reminder %s failed (attempt %s)", r.id, r.attempts)
            final = r.attempts >= MAX_ATTEMPTS
            async with engine.begin() as conn:
                await conn.execute(text("""
                    UPDATE reminders SET status = :st, last_error = :err,
                           remind_at = CASE WHEN :st = 'pending' THEN :retry ELSE remind_at END
                     WHERE id = :id"""),
                    {"st": "failed" if final else "pending", "err": str(e)[:500],
                     "retry": now + RETRY_DELAY, "id": r.id})
            continue
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE reminders SET status='sent', sent_at=:now, last_error=NULL WHERE id=:id"),
                               {"now": now, "id": r.id})
        sent += 1
    return sent


async def run_scheduler(engine: AsyncEngine, bot) -> None:
    """Forever loop; started by the bot on startup."""
    await recover_stuck(engine)
    logger.info("Reminder scheduler started (every %ss)", POLL_SECONDS)
    while True:
        try:
            n = await process_due(engine, bot)
            if n:
                logger.info("Sent %d reminder(s)", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reminder poll failed; will retry")
        await asyncio.sleep(POLL_SECONDS)
