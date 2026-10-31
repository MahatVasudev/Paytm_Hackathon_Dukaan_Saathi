from src.server.database.ops.utils import ResultEnvelope, ExecutionContext, to_rupees, to_paise
from src.server.types.pydantic import *
from src.server.database.sqltypes import party_balances, pending_actions, parties, transactions, party_aliases, reminders
from sqlalchemy import select, and_,insert, func, desc, asc, update
from sqlalchemy.dialects.postgresql import aggregate_order_by
import json
from datetime import timedelta, datetime, date, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict
import calendar
from src.server.utils.translit import search_key, transliterate, has_devanagari

IST = timezone(timedelta(hours=5, minutes=30))


def _aware(dt: datetime) -> datetime:
    """Treat naive datetimes as IST so they compare with ctx.now_ist."""
    return dt if dt.tzinfo else dt.replace(tzinfo=IST)


async def _stage_pending(ctx: ExecutionContext, intent: str, fields: dict, awaiting: str = "confirmation",
                         minutes: int = 10) -> int:
    """Insert a pending_actions row. The DB allows ONE 'open' action per business,
    so any older open action is cancelled first. Fields are JSON-safe (Decimal/date/Enum)."""
    await ctx.conn.execute(
        update(pending_actions)
        .where(pending_actions.c.business_id == ctx.business_id, pending_actions.c.status == "open")
        .values(status="cancelled")
    )
    res = await ctx.conn.execute(
        insert(pending_actions).values(
            business_id=ctx.business_id,
            intent=intent,
            collected_fields=json.loads(json.dumps(fields, default=str)),
            awaiting=awaiting,
            status="open",
            expires_at=ctx.now_ist + timedelta(minutes=minutes),
        ).returning(pending_actions.c.id)
    )
    return res.scalar_one()


async def _preview(ctx: ExecutionContext, kind: str, party, total_paise: int, paid_paise: int,
                   payment_status: str) -> dict:
    """Human-readable summary of a staged entry incl. the balance it would result in."""
    name, current, has_party = "Cash / no party", 0, False
    if party is not None and getattr(party, "ref_type", None) == "existing":
        pid = int(party.party_id)
        name, current, has_party = await _party_name(ctx, pid), await _balance_paise(ctx, pid), True
    elif party is not None and getattr(party, "ref_type", None) == "new":
        name, has_party = party.name, True
    delta = {"sale": total_paise - paid_paise, "purchase": -(total_paise - paid_paise),
             "payment_received": -total_paise, "payment_paid": total_paise, "expense": 0}[kind]
    prev = {"transaction_type": kind.replace("_", " ").capitalize(), "party_name": name,
            "amount": str(to_rupees(total_paise)), "payment_status": payment_status.replace("_", " ")}
    if has_party:
        after = current + delta
        prev.update(resulting_balance=str(to_rupees(abs(after))),
                    direction_text=_dir(after).replace("_", " "))
    return prev


def _dump(op) -> dict:
    return op.model_dump(mode="json", exclude_none=True)

def _dir(paise: int) -> str:
    return "they_owe_you" if paise > 0 else "you_owe_them" if paise < 0 else "settled"


async def _party_name(ctx: ExecutionContext, party_id: int) -> str:
    row = (await ctx.conn.execute(
        select(parties.c.name).where(parties.c.id == party_id, parties.c.business_id == ctx.business_id)
    )).fetchone()
    return row.name if row else "this party"


async def _balance_paise(ctx: ExecutionContext, party_id: int) -> int:
    row = (await ctx.conn.execute(
        select(party_balances.c.balance).where(
            party_balances.c.business_id == ctx.business_id, party_balances.c.party_id == party_id)
    )).fetchone()
    return int(row.balance) if row and row.balance else 0


async def execute_get_outstanding(op: GetOutstandingOp, ctx: ExecutionContext) -> ResultEnvelope:
    pid = int(op.party.party_id)
    balance = await _balance_paise(ctx, pid)

    open_count = (await ctx.conn.execute(
        select(func.count(transactions.c.id)).where(
            transactions.c.business_id == ctx.business_id, transactions.c.party_id == pid,
            transactions.c.status == "confirmed", transactions.c.type.in_(["sale", "purchase"]),
            transactions.c.paid_amount < transactions.c.total_amount)
    )).scalar_one()
    last_pay = (await ctx.conn.execute(
        select(func.max(transactions.c.transaction_date)).where(
            transactions.c.business_id == ctx.business_id, transactions.c.party_id == pid,
            transactions.c.status == "confirmed",
            transactions.c.type.in_(["payment_received", "payment_paid"]))
    )).scalar_one()

    return ResultEnvelope(status="ok", data={
        "party_id": str(pid),
        "party_name": await _party_name(ctx, pid),
        "balance": str(to_rupees(abs(balance))),
        "direction": _dir(balance),
        "open_entry_count": int(open_count) if balance != 0 else 0,
        "last_payment_date": last_pay.isoformat() if last_pay else None,
        "perspective": op.perspective.value,
    })


async def execute_propose_payment(op: ProposePaymentOp, ctx: ExecutionContext) -> ResultEnvelope:
    current_balance_paise = 0
    
    # 2. Extract party_id safely. If it's a new party, balance is 0.
    if op.party.ref_type == "existing":
        target_party_id = int(op.party.party_id)
        
        stmt = select(party_balances.c.balance).where(
            and_(
                party_balances.c.business_id == ctx.business_id,
                party_balances.c.party_id == target_party_id
            )
        )
        result = await ctx.conn.execute(stmt)
        row = result.fetchone()
        if row and row.balance:
            current_balance_paise = row.balance
            
    current_balance_rupees = to_rupees(current_balance_paise)

    # 3. Resolve 'settle_all' vs 'amount'
    if op.settle_all:
        if current_balance_paise == 0:
            return ResultEnvelope(status="invalid", error="Balance is already zero; nothing to settle.")
        payment_amount_rupees = abs(current_balance_rupees)
    else:
        payment_amount_rupees = op.amount

    # 4. Resolve 'auto' direction
    direction = op.direction.value
    if direction == "auto":
        if current_balance_paise == 0:
            return ResultEnvelope(
                status="needs_input", 
                data={"field": "direction", "options": ["Received", "Paid"]}
            )
        direction = "received" if current_balance_paise > 0 else "paid"

    # 5. Warn if paying more than owed (overpayment)
    warning = None
    if op.amount and op.amount > abs(current_balance_rupees):
        warning = f"This is ₹{op.amount - abs(current_balance_rupees)} more than the dues."

    # 6. Format payload
    payload = _dump(op)
    payload.update({
        "resolved_amount_paise": to_paise(payment_amount_rupees),
        "resolved_direction": direction,
        "expected_balance_paise": current_balance_paise 
    })

    action_id = await _stage_pending(ctx, "propose_payment", payload)
    pay_kind = "payment_received" if direction == "received" else "payment_paid"
    preview = await _preview(ctx, pay_kind, op.party, to_paise(payment_amount_rupees),
                             to_paise(payment_amount_rupees), "paid in full")

    return ResultEnvelope(status="ok", data={"action_id": str(action_id), "warning": warning, "preview": preview})


async def execute_aggregate(op: AggregateOp, ctx: ExecutionContext) -> ResultEnvelope:
    # 2. Base conditions: tenant isolation AND only confirmed transactions
    conditions = [
        transactions.c.business_id == ctx.business_id,
        transactions.c.status == "confirmed"
    ]
    
    # Apply Filters
    if op.from_date:
        conditions.append(transactions.c.transaction_date >= op.from_date)
    if op.to_date:
        conditions.append(transactions.c.transaction_date <= op.to_date)
        
    # Safely extract party_id if a specific party filter was provided
    if op.party and op.party.ref_type == "existing":
        conditions.append(transactions.c.party_id == int(op.party.party_id))
        
    if op.category:
        conditions.append(transactions.c.category == op.category)

    # 3. Resolve Metric using exact schema column names
    metric_value = op.metric.value
    if metric_value == "cash_in":
        metric_col = func.sum(transactions.c.paid_amount).filter(
            transactions.c.type.in_(["sale", "payment_received"])
        )
    elif metric_value == "cash_out":
        metric_col = func.sum(transactions.c.paid_amount).filter(
            transactions.c.type.in_(["purchase", "expense", "payment_paid"])
        )
    elif metric_value == "entry_count":
        metric_col = func.count(transactions.c.id)
    elif metric_value == "avg_entry_size":
        metric_col = func.avg(transactions.c.total_amount)
    else:
        # e.g., 'sales_total' -> 'sale'
        target_type = {
            "sales_total": "sale", "purchases_total": "purchase", "expenses_total": "expense",
            "payments_received_total": "payment_received", "payments_paid_total": "payment_paid",
        }[metric_value]
        metric_col = func.sum(transactions.c.total_amount).filter(
            transactions.c.type == target_type
        )

    # 4. Resolve Group By
    group_cols = []
    select_cols = [metric_col.label("value")]
    
    for dim in op.group_by:
        dim_val = dim.value
        if dim_val == "party":
            col = transactions.c.party_id
        elif dim_val in ("day", "week", "month"):
            # Works cleanly with Postgres Date columns
            col = func.date_trunc(dim_val, transactions.c.transaction_date)
        elif dim_val == "category":
            col = func.coalesce(transactions.c.category, "uncategorized")
        elif dim_val == "type":
            col = transactions.c.type
            
        group_cols.append(col)
        select_cols.append(col.label(f"dim_{dim_val}"))

    # 5. Build and Execute Query
    stmt = select(*select_cols).where(and_(*conditions))
    
    if group_cols:
        stmt = stmt.group_by(*group_cols)
        
    # Sort and Limit
    order_func = desc if op.order.value == "desc" else asc
    stmt = stmt.order_by(order_func("value")).limit(op.limit)

    result = await ctx.conn.execute(stmt)
    rows = result.fetchall()

    if not rows:
        return ResultEnvelope(status="not_found", data={"message": "no_activity"})

    # 5. Format Output
    formatted_data = []
    for row in rows:
        row_dict = dict(row._mapping)
        # Convert money metrics back to Rupees (entry_count stays as int)
        if metric_value != "entry_count":
            val = row_dict["value"]
            # Safely handle null sums (e.g. if the filtered set had no matches)
            row_dict["value"] = str(to_rupees(int(val))) if val is not None else "0.00"
        formatted_data.append(row_dict)

    return ResultEnvelope(status="ok", data={"rows": formatted_data})


async def execute_find_party(op: FindPartyOp, ctx: ExecutionContext) -> ResultEnvelope:
    # 1. Normalize the search string (lowercase, strip honorifics if your app logic dictates)
    search_term = search_key(op.name)   # Latin, lowercase, honorifics stripped (रमेश जी -> ramesh)
    
    # 2. Build the fuzzy search query using pg_trgm similarity
    # We join parties and party_balances to return the balance alongside the match
    alias_score = (
        select(func.max(func.similarity(party_aliases.c.normalized_alias, search_term)))
        .where(party_aliases.c.party_id == parties.c.id)
        .scalar_subquery()
    )
    stmt = select(
        parties.c.id,
        parties.c.name,
        parties.c.phone,
        func.greatest(
            func.similarity(parties.c.normalized_name, search_term),
            func.coalesce(alias_score, 0),
        ).label("score"),
        func.coalesce(party_balances.c.balance, 0).label("balance_paise")
    ).outerjoin(
        party_balances,
        and_(
            party_balances.c.business_id == parties.c.business_id,
            party_balances.c.party_id == parties.c.id
        )
    ).where(
        parties.c.business_id == ctx.business_id
    )

    if op.type_hint != PartyTypeHint.ANY:
        # Include 'both' in the filter if they ask for a specific type
        stmt = stmt.where(parties.c.type.in_([op.type_hint.value, "both"]))
        
    # Order by similarity score and limit
    stmt = stmt.order_by(desc("score")).limit(op.limit)
    
    result = await ctx.conn.execute(stmt)
    rows = result.fetchall()
    
    if not rows:
        return ResultEnvelope(status="not_found", data={"message": "No matching parties found.", "queried_name": op.name})
        
    # 3. Format output and apply a basic threshold filter
    candidates = []
    for r in rows:
        if r.score < 0.3: # Arbitrary threshold to ignore garbage matches
            continue
            
        candidates.append({
            "party_id": str(r.id),
            "name": r.name,
            "phone_last4": r.phone[-4:] if r.phone else None,
            "balance": str(to_rupees(abs(r.balance_paise))),
            "direction": "they_owe_you" if r.balance_paise > 0 else "you_owe_them" if r.balance_paise < 0 else "settled",
            "score": float(r.score)
        })

    if not candidates:
        return ResultEnvelope(status="not_found", data={"message": "No close matches found.", "queried_name": op.name})
        
    candidates.sort(key=lambda c: c["score"], reverse=True)
    # A clearly dominant match is treated as found rather than bothering the user
    if len(candidates) > 1 and candidates[0]["score"] >= 0.85 and candidates[0]["score"] - candidates[1]["score"] >= 0.25:
        candidates = candidates[:1]
    status = "party_found" if len(candidates) == 1 else "party_ambiguous"
    return ResultEnvelope(status=status, data={"candidates": candidates})


async def execute_list_all_dues(op: ListAllDuesOp, ctx: ExecutionContext) -> ResultEnvelope:
    # 1. Base conditions
    conditions = [
        party_balances.c.business_id == ctx.business_id,
        party_balances.c.balance != 0 # Ignore settled parties
    ]
    
    # 2. Filter by direction
    if op.direction == DuesDirection.THEY_OWE_YOU:
        conditions.append(party_balances.c.balance > 0)
    elif op.direction == DuesDirection.YOU_OWE_THEM:
        conditions.append(party_balances.c.balance < 0)
        
    # 3. Join with parties to get the name
    stmt = select(
        party_balances.c.party_id,
        parties.c.name,
        party_balances.c.balance
    ).join(
        parties,
        parties.c.id == party_balances.c.party_id
    ).where(and_(*conditions))
    
    # 4. Sorting
    if op.sort == DuesSort.AMOUNT_DESC:
        stmt = stmt.order_by(desc(func.abs(party_balances.c.balance)))
    # Note: 'oldest_first' requires a subquery or join against transactions to find the oldest unpaid date.
    
    stmt = stmt.limit(op.limit)
    
    result = await ctx.conn.execute(stmt)
    rows = result.fetchall()
    
    if not rows:
        return ResultEnvelope(status="ok", data={"message": "no_dues", "rows": []})
        
    formatted = [
        {
            "party_id": str(r.party_id),
            "name": r.name,
            "balance": str(to_rupees(abs(r.balance))),
            "direction": "they_owe_you" if r.balance > 0 else "you_owe_them"
        } for r in rows
    ]
    
    return ResultEnvelope(status="ok", data={"rows": formatted})


async def execute_list_transactions(op: ListTransactionsOp, ctx: ExecutionContext) -> ResultEnvelope:
    conditions = [transactions.c.business_id == ctx.business_id]
    
    if op.party and op.party.ref_type == "existing":
        conditions.append(transactions.c.party_id == int(op.party.party_id))
    
    if op.status.value != "any":
        conditions.append(transactions.c.status == op.status.value)
        
    if op.types:
        conditions.append(transactions.c.type.in_([t.value for t in op.types]))
        
    if op.from_date:
        conditions.append(transactions.c.transaction_date >= op.from_date)
    if op.to_date:
        conditions.append(transactions.c.transaction_date <= op.to_date)
        
    if op.text_contains:
        conditions.append(transactions.c.note.ilike(f"%{op.text_contains}%"))
        
    if op.min_amount:
        conditions.append(transactions.c.total_amount >= to_paise(op.min_amount))
    if op.max_amount:
        conditions.append(transactions.c.total_amount <= to_paise(op.max_amount))

    stmt = select(
        transactions.c.id,
        transactions.c.type,
        transactions.c.total_amount,
        transactions.c.paid_amount,
        transactions.c.transaction_date,
        transactions.c.note
    ).where(and_(*conditions))
    
    order_func = desc if op.order.value == "desc" else asc
    stmt = stmt.order_by(order_func(transactions.c.transaction_date), order_func(transactions.c.id))
    stmt = stmt.limit(op.limit).offset(op.offset)
    
    result = await ctx.conn.execute(stmt)
    rows = result.fetchall()
    
    if not rows:
        return ResultEnvelope(status="ok", data={"message": "no_results", "rows": []})
        
    formatted = [
        {
            "id": str(r.id),
            "type": r.type,
            "total": str(to_rupees(r.total_amount)),
            "paid": str(to_rupees(r.paid_amount)),
            "date": r.transaction_date.isoformat(),
            "note": r.note
        } for r in rows
    ]
    
    data = {"rows": formatted}
    if op.party and op.party.ref_type == "existing":
        data["party_name"] = await _party_name(ctx, int(op.party.party_id))
    return ResultEnvelope(status="ok", data=data)


async def execute_propose_sale(op: ProposeSaleOp, ctx: ExecutionContext) -> ResultEnvelope:
    # 1. Resolve money values to paise for storage in JSONB
    total_paise = to_paise(op.total)
    paid_paise = to_paise(op.paid_amount) if op.paid_amount else 0
    
    if op.payment_status.value == "paid_in_full":
        paid_paise = total_paise
    elif op.payment_status.value == "unpaid":
        paid_paise = 0
        
    if op.payment_status.value == "unknown":
        return ResultEnvelope(
            status="needs_input",
            data={"field": "payment_status", "options": ["Paid", "Udhaar", "Part-paid"]}
        )

    # 2. Stage the pending action
    payload = _dump(op)
    payload.update({
        "resolved_total_paise": total_paise,
        "resolved_paid_paise": paid_paise
    })
    action_id = await _stage_pending(ctx, "propose_sale", payload)
    
    prev = await _preview(ctx, "sale", op.party, total_paise, paid_paise, op.payment_status.value)
    return ResultEnvelope(status="ok", data={"action_id": str(action_id), "preview": prev})


async def execute_propose_expense(op: ProposeExpenseOp, ctx: ExecutionContext) -> ResultEnvelope:
    amount_paise = to_paise(op.amount)
    
    payload = _dump(op)
    payload.update({
        "resolved_total_paise": amount_paise,
        "resolved_paid_paise": amount_paise # Expenses are inherently fully paid
    })
    action_id = await _stage_pending(ctx, "propose_expense", payload)
    
    prev = await _preview(ctx, "expense", None, amount_paise, amount_paise, "paid in full")
    prev["party_name"] = op.category
    return ResultEnvelope(status="ok", data={"action_id": str(action_id), "preview": prev})

# ---------------------------------------------------------
# READS
# ---------------------------------------------------------
async def execute_get_transaction(op: GetTransactionOp, ctx: ExecutionContext) -> ResultEnvelope:
    stmt = select(transactions).where(
        and_(
            transactions.c.id == int(op.transaction_id),
            transactions.c.business_id == ctx.business_id
        )
    )
    result = await ctx.conn.execute(stmt)
    row = result.fetchone()
    
    if not row:
        return ResultEnvelope(status="not_found", data={"message": "Transaction not found."})
        
    formatted = dict(row._mapping)
    formatted["total_amount"] = str(to_rupees(formatted["total_amount"]))
    formatted["paid_amount"] = str(to_rupees(formatted["paid_amount"]))
    formatted["id"] = str(formatted["id"])
    formatted["party_id"] = str(formatted["party_id"]) if formatted["party_id"] else None
    
    return ResultEnvelope(status="ok", data={"transaction": formatted})

async def execute_get_last_entry(op: GetLastEntryOp, ctx: ExecutionContext) -> ResultEnvelope:
    # Check pending actions first if scope includes pending
    if op.scope.value in ("any", "pending"):
        p_conditions = [
            pending_actions.c.business_id == ctx.business_id,
            pending_actions.c.status == "open"
        ]
        # Map 'type' to intent for pending
        if op.type:
            p_conditions.append(pending_actions.c.intent == f"propose_{op.type.value}")
            
        p_stmt = select(pending_actions).where(and_(*p_conditions)).order_by(desc(pending_actions.c.created_at)).limit(1)
        p_result = await ctx.conn.execute(p_stmt)
        p_row = p_result.fetchone()
        if p_row:
            return ResultEnvelope(status="ok", data={"source": "pending", "id": str(p_row.id), "intent": p_row.intent})

    # Fallback to confirmed transactions
    if op.scope.value in ("any", "confirmed"):
        t_conditions = [
            transactions.c.business_id == ctx.business_id,
            transactions.c.status == "confirmed" # Skip voided
        ]
        if op.party and op.party.ref_type == "existing":
            t_conditions.append(transactions.c.party_id == int(op.party.party_id))
        if op.type:
            t_conditions.append(transactions.c.type == op.type.value)
        if op.within_hours:
            cutoff = ctx.now_ist - timedelta(hours=op.within_hours)
            t_conditions.append(transactions.c.created_at >= cutoff)

        t_stmt = select(transactions).where(and_(*t_conditions)).order_by(desc(transactions.c.created_at)).limit(1)
        t_result = await ctx.conn.execute(t_stmt)
        t_row = t_result.fetchone()
        
        if t_row:
            # Check if older than 24 hours to add the warning flag
            is_old = (ctx.now_ist - t_row.created_at).total_seconds() > 86400
            return ResultEnvelope(status="ok", data={
                "source": "confirmed", 
                "id": str(t_row.id), 
                "type": t_row.type,
                "is_older_than_24h": is_old
            })

    return ResultEnvelope(status="not_found", data={"message": "none_found"})

async def execute_list_reminders(op: ListRemindersOp, ctx: ExecutionContext) -> ResultEnvelope:
    conditions = [
        reminders.c.business_id == ctx.business_id,
        reminders.c.status == op.status.value
    ]
    if op.party and op.party.ref_type == "existing":
        conditions.append(reminders.c.party_id == int(op.party.party_id))
        
    stmt = select(reminders).where(and_(*conditions)).order_by(reminders.c.remind_at).limit(op.limit)
    result = await ctx.conn.execute(stmt)
    rows = result.fetchall()
    
    if not rows:
        return ResultEnvelope(status="not_found", data={"message": "none"})
        
    formatted = [{"id": str(r.id), "remind_at": r.remind_at.isoformat(), "note": r.note} for r in rows]
    return ResultEnvelope(status="ok", data={"reminders": formatted})

# ---------------------------------------------------------
# WRITES (Pending Actions)
# ---------------------------------------------------------
async def execute_propose_purchase(op: ProposePurchaseOp, ctx: ExecutionContext) -> ResultEnvelope:
    total_paise = to_paise(op.total)
    paid_paise = to_paise(op.paid_amount) if op.paid_amount else 0
    
    if op.payment_status.value == "paid_in_full":
        paid_paise = total_paise
    elif op.payment_status.value == "unpaid":
        paid_paise = 0
    elif op.payment_status.value == "unknown":
        return ResultEnvelope(status="needs_input", data={"field": "payment_status", "options": ["Paid", "Udhaar", "Part-paid"]})

    payload = _dump(op)
    payload.update({"resolved_total_paise": total_paise, "resolved_paid_paise": paid_paise})
    action_id = await _stage_pending(ctx, "propose_purchase", payload)
    prev = await _preview(ctx, "purchase", op.party, total_paise, paid_paise, op.payment_status.value)
    return ResultEnvelope(status="ok", data={"action_id": str(action_id), "preview": prev})

async def execute_propose_void(op: ProposeVoidOp, ctx: ExecutionContext) -> ResultEnvelope:
    target_id = int(op.transaction_id)
    # Check if exists & not already voided
    stmt = select(transactions.c.status, transactions.c.created_at).where(
        and_(transactions.c.id == target_id, transactions.c.business_id == ctx.business_id)
    )
    row = (await ctx.conn.execute(stmt)).fetchone()
    
    if not row:
        return ResultEnvelope(status="not_found", data={"message": "not found"})
    if row.status == "voided":
        return ResultEnvelope(status="invalid", error="Already voided.")
        
    warning = "Entry is older than 30 days." if (ctx.now_ist - row.created_at).days > 30 else None

    action_id = await _stage_pending(ctx, "propose_void", _dump(op))
    return ResultEnvelope(status="ok", data={"action_id": str(action_id), "warning": warning})

async def execute_propose_correction(op: ProposeCorrectionOp, ctx: ExecutionContext) -> ResultEnvelope:
    # A correction is essentially Void + Replace.
    target_id = int(op.transaction_id)
    stmt = select(transactions).where(
        and_(transactions.c.id == target_id, transactions.c.business_id == ctx.business_id)
    )
    row = (await ctx.conn.execute(stmt)).fetchone()
    
    if not row or row.status == "voided":
        return ResultEnvelope(status="invalid", error="Transaction not found or already voided.")

    action_id = await _stage_pending(ctx, "propose_correction", _dump(op))
    return ResultEnvelope(status="ok", data={"action_id": str(action_id)})

# ---------------------------------------------------------
# REMINDERS
# ---------------------------------------------------------
async def execute_create_reminder(op: CreateReminderOp, ctx: ExecutionContext) -> ResultEnvelope:
    remind_at = _aware(op.remind_at)
    if remind_at < ctx.now_ist:
        if (ctx.now_ist - remind_at) <= timedelta(minutes=5):
            remind_at = ctx.now_ist + timedelta(seconds=30)      # LLM/network lag: send ASAP
        else:
            when = remind_at.astimezone(IST).strftime("%d %b, %I:%M %p").lstrip("0")
            return ResultEnvelope(status="needs_input", data={
                "field": "time",
                "message": f"{when} has already passed. When should I remind you? (e.g. tomorrow 5pm, in 2 hours)"})
        
    warning = "Reminder is more than a year ahead." if (remind_at - ctx.now_ist).days > 365 else None
    
    party_id_val = int(op.party.party_id) if op.party and op.party.ref_type == "existing" else None

    # Check for duplicates on the same day for same party
    if party_id_val:
        dup_stmt = select(reminders.c.id).where(
            and_(
                reminders.c.business_id == ctx.business_id,
                reminders.c.party_id == party_id_val,
                reminders.c.status == "pending",
                func.date(reminders.c.remind_at) == remind_at.date()
            )
        )
        if (await ctx.conn.execute(dup_stmt)).fetchone():
            return ResultEnvelope(status="conflict", error="A reminder already exists for this party on this date.")

    stmt = insert(reminders).values(
        business_id=ctx.business_id,
        party_id=party_id_val,
        note=op.note,
        remind_at=remind_at,
        status="pending"
    ).returning(reminders.c.id)
    
    res = await ctx.conn.execute(stmt)
    return ResultEnvelope(status="ok", data={"reminder_id": str(res.scalar_one()), "warning": warning,
                                              "remind_at": remind_at.astimezone(IST).isoformat(), "note": op.note})

async def execute_reschedule_reminder(op: RescheduleReminderOp, ctx: ExecutionContext) -> ResultEnvelope:
    stmt = update(reminders).where(
        and_(
            reminders.c.id == int(op.reminder_id),
            reminders.c.business_id == ctx.business_id,
            reminders.c.status == "pending"
        )
    ).values(remind_at=_aware(op.remind_at)).returning(reminders.c.id)
    
    res = await ctx.conn.execute(stmt)
    if not res.fetchone():
        return ResultEnvelope(status="invalid", error="Reminder not found or no longer pending.")
        
    return ResultEnvelope(status="ok", data={"status": "rescheduled"})

async def execute_cancel_reminder(op: CancelReminderOp, ctx: ExecutionContext) -> ResultEnvelope:
    conditions = [reminders.c.business_id == ctx.business_id, reminders.c.status == "pending"]
    if op.reminder_id:
        conditions.append(reminders.c.id == int(op.reminder_id))
    elif op.party and op.party.ref_type == "existing":
        conditions.append(reminders.c.party_id == int(op.party.party_id))
        
    stmt = update(reminders).where(and_(*conditions)).values(status="cancelled").returning(reminders.c.id)
    res = await ctx.conn.execute(stmt)
    rows = res.fetchall()
    
    if not rows:
        return ResultEnvelope(status="not_found", error="No matching pending reminders found.")
        
    return ResultEnvelope(status="ok", data={"cancelled_count": len(rows)})

# ---------------------------------------------------------
# UTILITY & CONTROL
# ---------------------------------------------------------
def _month_bounds(y: int, m: int):
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


import re as _re

_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUMWORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
             "ek": 1, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "pach": 5,
             "chhe": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10, "pandrah": 15, "bees": 20, "tees": 30}
_UNITS = [  # (regex, seconds)
    (r"sec(?:ond)?s?|sek(?:and|end)s?|secs?", 1),
    (r"min(?:ute)?s?|mins?|minat|minut", 60),
    (r"hours?|hrs?|ghant[aei]|ghanta", 3600),
    (r"days?|din", 86400),
    (r"weeks?|hafta|hafte|haftे", 604800),
]
_UNIT_RE = "|".join(f"(?P<u{i}>{rx})" for i, (rx, _) in enumerate(_UNITS))
_DUR_PART = _re.compile(rf"(?P<n>\d+(?:\.\d+)?|{'|'.join(_NUMWORDS)})\s*(?:{_UNIT_RE})\b")
_DUR_NOISE = _re.compile(r"\b(in|from now|from|now|after|within|later|baad|bad|ke baad|ke bad|mein|me|and|aur)\b")
_HALF = _re.compile(r"\b(half an hour|half hour|aadha ghanta|adha ghanta|aadhe ghante|aadha ghante)\b")


def _humanize(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    for size, name in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if secs >= size and secs % size == 0:
            n = secs // size
            return f"{n} {name}{'s' if n != 1 else ''}"
    if secs >= 120:
        return f"{secs // 60} min {secs % 60} sec" if secs % 60 else f"{secs // 60} minutes"
    return f"{secs} second{'s' if secs != 1 else ''}"


def _parse_duration(exp: str):
    """'in 10 seconds from now', '2 ghante baad', 'an hour', 'half an hour', '1 hour 30 minutes' -> timedelta.
    Returns None unless the WHOLE phrase is a duration."""
    text = exp
    total = 0.0
    if _HALF.search(text):
        total += 1800
        text = _HALF.sub(" ", text)
    if _re.search(r"\bdedh ghant[aei]\b", text):
        total += 5400
        text = _re.sub(r"\bdedh ghant[aei]\b", " ", text)
    found = False
    def take(m):
        nonlocal total, found
        n = m.group("n")
        n = float(n) if n[0].isdigit() else _NUMWORDS[n]
        for i, (_, secs) in enumerate(_UNITS):
            if m.group(f"u{i}"):
                total += n * secs
                found = True
        return " "
    text = _DUR_PART.sub(take, text)
    text = _DUR_NOISE.sub(" ", text)
    if (not found and total == 0) or text.strip():
        return None
    return timedelta(seconds=total)


_TIME = _re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|bje)?\b")
# part of day -> (default hour, whether a bare hour 1-11 beside it means PM)
_PARTS = {"subah": (9, False), "savere": (9, False), "savera": (9, False), "morning": (9, False),
          "dopahar": (14, True), "dophar": (14, True), "afternoon": (14, True),
          "shaam": (18, True), "sham": (18, True), "evening": (18, True),
          "raat": (21, True), "rat": (21, True), "night": (21, True), "tonight": (21, True)}
_WEEKDAYS = {"monday": 0, "somvar": 0, "somwar": 0, "tuesday": 1, "mangalvar": 1, "mangalwar": 1,
             "wednesday": 2, "budhvar": 2, "budhwar": 2, "thursday": 3, "guruvar": 3, "guruwar": 3,
             "veervar": 3, "friday": 4, "shukravar": 4, "shukrawar": 4, "saturday": 5, "shanivar": 5,
             "shaniwar": 5, "sunday": 6, "ravivar": 6, "raviwar": 6, "itwar": 6, "itvar": 6}
_FILLER = {"at", "ko", "pe", "par", "ka", "ki", "ke", "on", "next", "agle", "agla", "aane", "wale", "wala", "is", "this"}


def _normalize(exp: str) -> str:
    """Devanagari -> Latin, Devanagari digits -> ASCII, lowercase, squeeze spaces."""
    exp = transliterate(exp.translate(_DIGITS)) if has_devanagari(exp) else exp
    exp = exp.lower().replace("बजे", "baje")
    return " ".join(exp.replace(",", " ").split())


def _find_time(exp: str):
    """First number that is clearly a time (am/pm/baje or hh:mm), ignoring date digits like 2026-11-05."""
    for m in _TIME.finditer(exp):
        before = exp[max(0, m.start() - 1):m.start()]
        if before in ("-", "/"):
            continue
        if m.group(3) or m.group(2):
            return m
    return None


def _extract_time(exp: str):
    """Pull a time of day out of the phrase -> ((hour, minute) | None, remaining text)."""
    part = next((w for w in _PARTS if _re.search(rf"\b{w}\b", exp)), None)
    default_h, pm_bias = _PARTS[part] if part else (None, False)
    m = _find_time(exp)
    if m:
        h, mi, suffix = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if h > 23 or mi > 59:
            return None, exp
        if suffix == "pm" and h < 12:
            h += 12
        elif suffix == "am" and h == 12:
            h = 0
        elif suffix in (None, "baje", "bje") and h <= 12:
            if part and pm_bias and h < 12:
                h += 12                                  # "shaam 5 baje", "raat 9 baje"
            elif not part and suffix in ("baje", "bje"):
                h = h + 12 if 1 <= h <= 6 else h         # "5 baje" -> shop hours
        exp = exp[:m.start()] + " " + exp[m.end():]
        hm = (h, mi)
    elif part:
        hm = (default_h, 0)
    else:
        return None, " ".join(t for t in exp.split() if t not in _FILLER)
    if part:
        exp = _re.sub(rf"\b{part}\b", " ", exp)
    return hm, " ".join(t for t in exp.split() if t not in _FILLER)


def _month_bounds(y: int, m: int):
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def _next_weekday(today: date, wd: int) -> date:
    return today + timedelta(days=(wd - today.weekday()) % 7 or 7)


async def execute_resolve_date(op: ResolveDateOp, ctx: ExecutionContext) -> ResultEnvelope:
    """Python-side resolution of English/Hinglish/Devanagari date phrases (no LLM date math)."""
    exp = _normalize(op.expression)
    now = ctx.now_ist
    today = now.date()
    is_dt = op.mode.value == "datetime"
    prefer = op.prefer.value
    if is_dt and prefer == "auto":
        prefer = "future"                        # reminders are never in the past ("kal" = tomorrow)

    def ask_when():
        return ResultEnvelope(status="needs_input", data={
            "field": "time", "message": "I didn't catch when. When should I remind you? (e.g. tomorrow 5pm)"})

    # ---- relative durations: "in 10 seconds from now", "2 ghante baad", "half an hour", ...
    if is_dt:
        delta = _parse_duration(exp)
        if delta is not None:
            when = (now + delta).replace(microsecond=0)
            return ResultEnvelope(status="ok", data={
                "assumption": "In " + _humanize(delta),
                "resolved_date": when.date().isoformat(), "resolved_from": when.date().isoformat(),
                "resolved_to": when.date().isoformat(), "resolved_datetime": when.isoformat()})

    time_of_day, exp = _extract_time(exp) if is_dt else (None, exp)

    d_from = d_to = None
    assumption = ""
    recognized = True

    def one(d: date, label: str):
        nonlocal d_from, d_to, assumption
        d_from = d_to = d
        assumption = label

    if exp in ("", "today", "aaj", "aj", "tonight"):
        one(today, "Today")
    elif exp in ("day after tomorrow", "parso", "parson", "parsoon") and (prefer != "past" or exp == "day after tomorrow"):
        one(today + timedelta(days=2), "Day after tomorrow")
    elif exp in ("yesterday", "beeta hua kal", "kal") and not (exp == "kal" and prefer == "future"):
        one(today - timedelta(days=1), "Yesterday")
    elif exp in ("tomorrow", "kal", "aane wala kal"):
        one(today + timedelta(days=1), "Tomorrow")
    elif exp in ("parso", "parson", "day before yesterday"):
        one(today - timedelta(days=2), "Day before yesterday")
    elif exp in _WEEKDAYS:
        one(_next_weekday(today, _WEEKDAYS[exp]), exp.capitalize())
    elif exp in ("this week", "is hafte", "is week", "hafte"):
        d_from, d_to = today - timedelta(days=today.weekday()), today; assumption = "This week (Mon to today)"
    elif exp in ("last week", "pichle hafte", "pichhle hafte"):
        end = today - timedelta(days=today.weekday() + 1)
        d_from, d_to = end - timedelta(days=6), end; assumption = "Last week (Mon-Sun)"
    elif exp in ("this month", "is mahine", "is month", "mahine"):
        d_from, d_to = _month_bounds(today.year, today.month)[0], today; assumption = "This month so far"
    elif exp in ("last month", "pichle mahine", "pichhle mahine"):
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        d_from, d_to = _month_bounds(y, m); assumption = "Last month"
    elif exp in ("this year", "is saal"):
        d_from, d_to = date(today.year, 1, 1), today; assumption = "This year so far"
    elif exp.startswith("last ") and exp.endswith(" days") and exp[5:-5].strip().isdigit():
        n = int(exp[5:-5]); d_from, d_to = today - timedelta(days=n - 1), today; assumption = f"Last {n} days"
    else:
        months = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
        months.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
        try:
            one(date.fromisoformat(exp), exp)
        except ValueError:
            if exp in months:
                m = months[exp]
                y = today.year if m <= today.month else today.year - 1
                d_from, d_to = _month_bounds(y, m); assumption = f"{calendar.month_name[m]} {y}"
            else:
                recognized = False

    if not recognized:
        if is_dt:
            return ask_when()                    # never guess a reminder time
        one(today, f"Assuming today for '{exp}'")

    data = {"assumption": assumption, "resolved_date": d_from.isoformat(),
            "resolved_from": d_from.isoformat(), "resolved_to": d_to.isoformat()}
    if is_dt:
        explicit_date = exp not in ("", "today", "aaj", "aj", "tonight")
        if time_of_day:
            hh, mm = time_of_day
        elif d_from == today:                    # "remind me today" with no time: next full hour
            nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            hh, mm = nxt.hour, 0
            if nxt.date() != today:
                return ask_when()
        else:
            hh, mm = 10, 0
        when = datetime(d_from.year, d_from.month, d_from.day, hh, mm, tzinfo=IST)
        if time_of_day and when <= now and d_from == today and not explicit_date:
            when += timedelta(days=1)            # "5pm" said after 5pm means tomorrow
            assumption = "Tomorrow"
        data.update(resolved_datetime=when.isoformat(), resolved_date=when.date().isoformat(),
                    assumption=f"{assumption} {when.strftime('%I:%M %p').lstrip('0')}")
    return ResultEnvelope(status="ok", data=data)

async def execute_calculate(op: CalculateOp, ctx: ExecutionContext) -> ResultEnvelope:
    # Operands are pre-resolved $vars or Decimals due to Pydantic/ExecutionContext pipeline
    ops = [Decimal(str(x)) for x in op.operands]
    result_val = Decimal("0")
    
    if op.calc_op == CalcOp.ADD:
        result_val = sum(ops)
    elif op.calc_op == CalcOp.SUBTRACT:
        result_val = ops[0] - ops[1]
    elif op.calc_op == CalcOp.MULTIPLY:
        result_val = ops[0] * ops[1]
    elif op.calc_op == CalcOp.DIVIDE:
        if ops[1] == 0:
            return ResultEnvelope(status="invalid", error="Division by zero")
        result_val = ops[0] / ops[1]
    elif op.calc_op == CalcOp.PERCENT_OF:
        result_val = (ops[0] / Decimal("100")) * ops[1]
    elif op.calc_op == CalcOp.PCT_CHANGE:
        if ops[0] == 0:
            return ResultEnvelope(status="ok", data={"result": "0.00", "is_undefined": True})
        result_val = (ops[1] - ops[0]) / ops[0] * Decimal("100")
    elif op.calc_op == CalcOp.MIN:
        result_val = min(ops)
    elif op.calc_op == CalcOp.MAX:
        result_val = max(ops)
    
    # Half-up rounding to 2 decimals
    final_val = result_val.quantize(Decimal("0.01"))
    return ResultEnvelope(status="ok", data={"result": str(final_val)})

async def execute_ask_user(op: AskUserOp, ctx: ExecutionContext) -> ResultEnvelope:
    """
    Stages an explicit question for the user (e.g., confirming an assumption or picking an option).
    Halts execution and returns 'needs_input'.
    """
    
    awaiting_val = op.kind.value if hasattr(op.kind, "value") else op.kind
    action_id = await _stage_pending(ctx, "ask_user", op.model_dump(mode="json"), awaiting=awaiting_val, minutes=60)

    # 3. Return the envelope instructing the orchestrator to halt and ask the user
    # The dictionary provided to `data` maps directly to what your dispatch_orchestrator_result expects
    return ResultEnvelope(
        status="needs_input",
        data={
            "action_id": str(action_id),
            "kind": awaiting_val,
            "field": op.field,
            # Ensure nested models (like AskOption) are converted to dicts for the orchestrator
            "options": [opt.model_dump() if hasattr(opt, "model_dump") else opt for opt in op.options],
            "slots": op.slots
        }
    )

async def execute_edit_pending(op: EditPendingOp, ctx: ExecutionContext) -> ResultEnvelope:
    """Merge changes into the business's single open pending action."""
    row = (await ctx.conn.execute(
        select(pending_actions).where(
            pending_actions.c.business_id == ctx.business_id,
            pending_actions.c.status == "open",
        ).order_by(desc(pending_actions.c.created_at)).limit(1)
    )).fetchone()
    if not row:
        return ResultEnvelope(status="not_found", data={"message": "No pending entry to edit."})

    fields = dict(row.collected_fields or {})
    changes = op.changes.model_dump(mode="json", exclude_none=True)
    fields.update(changes)
    if "total" in changes:
        fields["resolved_total_paise"] = to_paise(Decimal(str(changes["total"])))
    status = fields.get("payment_status")
    if status == "paid_in_full" and "resolved_total_paise" in fields:
        fields["resolved_paid_paise"] = fields["resolved_total_paise"]
    elif status == "unpaid":
        fields["resolved_paid_paise"] = 0
    elif "paid_amount" in changes:
        fields["resolved_paid_paise"] = to_paise(Decimal(str(changes["paid_amount"])))

    await ctx.conn.execute(
        update(pending_actions).where(pending_actions.c.id == row.id)
        .values(collected_fields=json.loads(json.dumps(fields, default=str)),
                expires_at=ctx.now_ist + timedelta(minutes=10))
    )
    return ResultEnvelope(status="ok", data={"action_id": str(row.id)})


async def execute_reply(op: ReplyOp, ctx: ExecutionContext) -> ResultEnvelope:
    # Signals the end of the plan with a template response
    return ResultEnvelope(status="ok", data={"template_id": op.template_id, "slots": op.slots})


# ---------------------------------------------------------
# CONFIRM / CANCEL a staged pending action (the only place ledger rows are written)
# ---------------------------------------------------------
def _e164(phone):
    if not phone:
        return None
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if str(phone).startswith("+") and 7 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+91" + digits
    return None


async def _party_id_from_ref(ctx: ExecutionContext, ref, default_type: str):
    """existing -> id; new -> reuse exact-name party or create it; none/missing -> None."""
    if not isinstance(ref, dict) or ref.get("ref_type") == "none":
        return None
    if ref.get("ref_type") == "existing":
        return int(ref["party_id"])
    name = ref["name"].strip()
    if has_devanagari(name):
        name = transliterate(name).title()      # keep stored names in one script so matching stays reliable
    norm = search_key(name)
    row = (await ctx.conn.execute(
        select(parties.c.id).where(parties.c.business_id == ctx.business_id, parties.c.normalized_name == norm).limit(1)
    )).fetchone()
    if row:
        return row.id
    res = await ctx.conn.execute(insert(parties).values(
        business_id=ctx.business_id, name=name, normalized_name=norm,
        phone=_e164(ref.get("phone")), type=ref.get("type") or default_type,
        created_at=ctx.now_ist,
    ).returning(parties.c.id))
    return res.scalar_one()


async def execute_confirm_pending(ctx: ExecutionContext, action_id: int | None = None,
                                  cancel: bool = False) -> ResultEnvelope:
    conds = [pending_actions.c.business_id == ctx.business_id, pending_actions.c.status == "open"]
    if action_id is not None:
        conds.append(pending_actions.c.id == action_id)
    row = (await ctx.conn.execute(
        select(pending_actions).where(*conds).order_by(desc(pending_actions.c.created_at)).limit(1)
    )).fetchone()
    if not row:
        return ResultEnvelope(status="not_found", data={"message": "Nothing is waiting for confirmation."})

    async def close(status):
        await ctx.conn.execute(update(pending_actions).where(pending_actions.c.id == row.id).values(status=status))

    if cancel:
        await close("cancelled")
        return ResultEnvelope(status="ok", data={"cancelled": True, "intent": row.intent})
    if row.expires_at < ctx.now_ist:
        await close("expired")
        return ResultEnvelope(status="invalid", error="That entry expired. Please send it again.")
    if row.intent not in ("propose_sale", "propose_purchase", "propose_expense", "propose_payment",
                          "propose_void", "propose_correction"):
        return ResultEnvelope(status="not_found", data={"message": "Nothing is waiting for confirmation."})

    f = row.collected_fields or {}
    now = ctx.now_ist
    txn_date = date.fromisoformat(f["date"]) if f.get("date") else now.date()
    intent = row.intent
    summary: Dict[str, Any] = {"intent": intent}

    if intent in ("propose_sale", "propose_purchase", "propose_expense", "propose_payment"):
        if intent == "propose_sale":
            ttype, default_pt = "sale", "customer"
        elif intent == "propose_purchase":
            ttype, default_pt = "purchase", "supplier"
        elif intent == "propose_expense":
            ttype, default_pt = "expense", "customer"
        else:
            ttype = "payment_received" if f.get("resolved_direction") == "received" else "payment_paid"
            default_pt = "customer" if ttype == "payment_received" else "supplier"
        party_id = None if intent == "propose_expense" else await _party_id_from_ref(ctx, f.get("party"), default_pt)
        if intent == "propose_payment":
            total = int(f["resolved_amount_paise"]); paid = total
        else:
            total = int(f["resolved_total_paise"]); paid = int(f["resolved_paid_paise"])
        res = await ctx.conn.execute(insert(transactions).values(
            business_id=ctx.business_id, party_id=party_id, type=ttype,
            total_amount=total, paid_amount=paid, category=f.get("category"),
            note=f.get("note") or f.get("item_text"), transaction_date=txn_date,
            status="confirmed", source="text", created_at=now, confirmed_at=now,
        ).returning(transactions.c.id))
        summary.update(transaction_id=str(res.scalar_one()), type=ttype,
                       total=str(to_rupees(total)), paid=str(to_rupees(paid)))
        if party_id:
            bal = (await ctx.conn.execute(select(party_balances.c.balance, ).where(
                party_balances.c.business_id == ctx.business_id, party_balances.c.party_id == party_id))).fetchone()
            b = bal.balance if bal and bal.balance else 0
            summary.update(balance=str(to_rupees(abs(b))),
                           direction="they_owe_you" if b > 0 else "you_owe_them" if b < 0 else "settled")

    elif intent == "propose_void":
        res = await ctx.conn.execute(update(transactions).where(
            transactions.c.id == int(f["transaction_id"]), transactions.c.business_id == ctx.business_id,
            transactions.c.status != "voided",
        ).values(status="voided", voided_at=now).returning(transactions.c.id))
        if not res.fetchone():
            return ResultEnvelope(status="invalid", error="That entry was not found or is already voided.")
        summary.update(transaction_id=str(f["transaction_id"]), voided=True)

    else:  # propose_correction
        ch = f.get("changes", {})
        cur = (await ctx.conn.execute(select(transactions).where(
            transactions.c.id == int(f["transaction_id"]), transactions.c.business_id == ctx.business_id,
            transactions.c.status == "confirmed"))).fetchone()
        if not cur:
            return ResultEnvelope(status="invalid", error="That entry was not found or is voided.")
        vals: Dict[str, Any] = {}
        total = to_paise(Decimal(str(ch["total"]))) if "total" in ch else cur.total_amount
        paid = cur.paid_amount
        if ch.get("payment_status") == "paid_in_full" or cur.type in ("expense", "payment_received", "payment_paid"):
            paid = total
        elif ch.get("payment_status") == "unpaid":
            paid = 0
        elif "paid_amount" in ch:
            paid = to_paise(Decimal(str(ch["paid_amount"])))
        elif "total" in ch and cur.paid_amount > total:
            paid = total
        vals.update(total_amount=total, paid_amount=paid)
        if "party" in ch:
            vals["party_id"] = await _party_id_from_ref(ctx, ch["party"], "customer")
        if "date" in ch:
            vals["transaction_date"] = date.fromisoformat(ch["date"])
        for k in ("note", "category"):
            if k in ch:
                vals[k] = ch[k]
        await ctx.conn.execute(update(transactions).where(transactions.c.id == cur.id).values(**vals))
        summary.update(transaction_id=str(cur.id), corrected=True)

    await close("completed")
    return ResultEnvelope(status="ok", data={"committed": True, **summary})


# - [x] find party
# - [x] get_outstanding
# - [x] list_all_dues
# - [x] list_transactions
# - [x] get_transactions
# - [x] aggregate
# - [x] get_last_entry
# - [x] list_reminders
# - [x] propose_sale
# - [x] propose_purchase
# - [x] propose_expense
# - [x] propose_payment
# - [x] propose_void
# - [x] propose_correction
# - [x] edit_pending
# - [x] create_reminder
# - [x] reschedule_reminder
# - [x] cancel_reminder
# - [x] resolve_date
# - [x] calculate
# - [x] ask_user
# - [x] reply
