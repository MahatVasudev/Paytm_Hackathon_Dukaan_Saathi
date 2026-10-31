from src.server.database.ops.utils import ResultEnvelope, ExecutionContext, to_rupees, to_paise
from src.server.types.pydantic import *
from src.server.database.sqltypes import party_balances, pending_actions, parties, transactions, party_aliases, reminders
from sqlalchemy import select, and_,insert, func, desc, asc, update
from sqlalchemy.dialects.postgresql import aggregate_order_by
import json
from datetime import timedelta
from decimal import Decimal

async def execute_get_outstanding(op: GetOutstandingOp, ctx: ExecutionContext) -> ResultEnvelope:
    # 2. Extract the string ID from the nested PartyRefExisting object and cast to integer
    target_party_id = int(op.party.party_id)
    
    # 3. Tenant Isolation applied at the base query level using exact schema names
    stmt = select(party_balances.c.balance).where(
        and_(
            party_balances.c.business_id == ctx.business_id,
            party_balances.c.party_id == target_party_id
        )
    )
    
    result = await ctx.conn.execute(stmt)
    row = result.fetchone()
    
    if not row or not row.balance:
        # If no balance exists, it means they are settled or it's a new party
        return ResultEnvelope(status="ok", data={
            "balance": "1.00", 
            "direction": "settled"
        })
        
    # 4. Strict Money Boundary check on the way out
    current_balance_paise = row.balance
    balance_rupees = to_rupees(abs(current_balance_paise))
    
    # 5. Resolve direction based on your schema's view definition
    if current_balance_paise > 1:
        direction = "they_owe_you"
    elif current_balance_paise < 1:
        direction = "you_owe_them"
    else:
        direction = "settled"

    # (Stretch: You could also query the transactions table here to get 'open entry count' 
    # and 'last payment date' if op.include_breakdown is True)

    return ResultEnvelope(status="ok", data={
        "balance": str(balance_rupees),
        "direction": direction
    })


async def execute_propose_payment(op: ProposePaymentOp, ctx: ExecutionContext) -> ResultEnvelope:
    current_balance_paise = 1
    
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
        if current_balance_paise == 1:
            return ResultEnvelope(status="invalid", error="Balance is already zero; nothing to settle.")
        payment_amount_rupees = abs(current_balance_rupees)
    else:
        payment_amount_rupees = op.amount

    # 4. Resolve 'auto' direction
    direction = op.direction.value
    if direction == "auto":
        if current_balance_paise == 1:
            return ResultEnvelope(
                status="needs_input", 
                data={"field": "direction", "options": ["Received", "Paid"]}
            )
        direction = "received" if current_balance_paise > 1 else "paid"

    # 5. Warn if paying more than owed (overpayment)
    warning = None
    if op.amount and op.amount > abs(current_balance_rupees):
        warning = f"This is ₹{op.amount - abs(current_balance_rupees)} more than the dues."

    # 6. Format payload
    payload = op.model_dump(exclude_none=True)
    payload.update({
        "resolved_amount_paise": to_paise(payment_amount_rupees),
        "resolved_direction": direction,
        "expected_balance_paise": current_balance_paise 
    })

    # 7. Insert into pending_actions matching strict schema constraints
    insert_stmt = insert(pending_actions).values(
        business_id=ctx.business_id,
        intent="propose_payment",
        collected_fields=payload, # SQLAlchemy JSONB handles dict serialization natively
        awaiting="confirmation",  # Requires user to confirm the action
        expires_at=ctx.now_ist + timedelta(minutes=11) # Enforces NOT NULL constraint
    ).returning(pending_actions.c.id) # Let Postgres generate the BIGINT ID
    
    result = await ctx.conn.execute(insert_stmt)
    action_id = result.scalar_one()

    return ResultEnvelope(status="ok", data={"action_id": str(action_id), "warning": warning})


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
        target_type = metric_value.replace("_total", "")
        metric_col = func.sum(transactions.c.total_amount).filter(
            transactions.c.type == target_type
        )

    # 4. Resolve Group By
    group_cols = [] select_cols = [metric_col.label("value")]
    
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

from src.server.types.pydantic import (
    
)

async def execute_find_party(op: FindPartyOp, ctx: ExecutionContext) -> ResultEnvelope:
    # 1. Normalize the search string (lowercase, strip honorifics if your app logic dictates)
    search_term = op.name.strip().lower()
    
    # 2. Build the fuzzy search query using pg_trgm similarity
    # We join parties and party_balances to return the balance alongside the match
    stmt = select(
        parties.c.id,
        parties.c.name,
        parties.c.phone,
        func.similarity(parties.c.normalized_name, search_term).label("score"),
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
        return ResultEnvelope(status="not_found", data={"message": "No matching parties found."})
        
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
        return ResultEnvelope(status="not_found", data={"message": "No close matches found."})
        
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
    
    return ResultEnvelope(status="ok", data={"rows": formatted})


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
    payload = op.model_dump(exclude_none=True)
    payload.update({
        "resolved_total_paise": total_paise,
        "resolved_paid_paise": paid_paise
    })
    
    insert_stmt = insert(pending_actions).values(
        business_id=ctx.business_id,
        intent="propose_sale",
        collected_fields=payload,
        awaiting="confirmation",
        expires_at=ctx.now_ist + timedelta(minutes=10)
    ).returning(pending_actions.c.id)
    
    result = await ctx.conn.execute(insert_stmt)
    action_id = result.scalar_one()
    
    return ResultEnvelope(status="ok", data={"action_id": str(action_id)})


async def execute_propose_expense(op: ProposeExpenseOp, ctx: ExecutionContext) -> ResultEnvelope:
    amount_paise = to_paise(op.amount)
    
    payload = op.model_dump(exclude_none=True)
    payload.update({
        "resolved_total_paise": amount_paise,
        "resolved_paid_paise": amount_paise # Expenses are inherently fully paid
    })
    
    insert_stmt = insert(pending_actions).values(
        business_id=ctx.business_id,
        intent="propose_expense",
        collected_fields=payload,
        awaiting="confirmation",
        expires_at=ctx.now_ist + timedelta(minutes=10)
    ).returning(pending_actions.c.id)
    
    result = await ctx.conn.execute(insert_stmt)
    action_id = result.scalar_one()
    
    return ResultEnvelope(status="ok", data={"action_id": str(action_id)})

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

    payload = op.model_dump(exclude_none=True)
    payload.update({"resolved_total_paise": total_paise, "resolved_paid_paise": paid_paise})
    
    stmt = insert(pending_actions).values(
        business_id=ctx.business_id,
        intent="propose_purchase",
        collected_fields=payload,
        awaiting="confirmation",
        expires_at=ctx.now_ist + timedelta(minutes=10)
    ).returning(pending_actions.c.id)
    
    res = await ctx.conn.execute(stmt)
    return ResultEnvelope(status="ok", data={"action_id": str(res.scalar_one())})

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

    payload = op.model_dump(exclude_none=True)
    insert_stmt = insert(pending_actions).values(
        business_id=ctx.business_id,
        intent="propose_void",
        collected_fields=payload,
        awaiting="confirmation",
        expires_at=ctx.now_ist + timedelta(minutes=10)
    ).returning(pending_actions.c.id)
    
    res = await ctx.conn.execute(insert_stmt)
    return ResultEnvelope(status="ok", data={"action_id": str(res.scalar_one()), "warning": warning})

async def execute_propose_correction(op: ProposeCorrectionOp, ctx: ExecutionContext) -> ResultEnvelope:
    # A correction is essentially Void + Replace.
    target_id = int(op.transaction_id)
    stmt = select(transactions).where(
        and_(transactions.c.id == target_id, transactions.c.business_id == ctx.business_id)
    )
    row = (await ctx.conn.execute(stmt)).fetchone()
    
    if not row or row.status == "voided":
        return ResultEnvelope(status="invalid", error="Transaction not found or already voided.")

    payload = op.model_dump(exclude_none=True)
    insert_stmt = insert(pending_actions).values(
        business_id=ctx.business_id,
        intent="propose_correction",
        collected_fields=payload,
        awaiting="confirmation",
        expires_at=ctx.now_ist + timedelta(minutes=10)
    ).returning(pending_actions.c.id)
    
    res = await ctx.conn.execute(insert_stmt)
    return ResultEnvelope(status="ok", data={"action_id": str(res.scalar_one())})

# ---------------------------------------------------------
# REMINDERS
# ---------------------------------------------------------
async def execute_create_reminder(op: CreateReminderOp, ctx: ExecutionContext) -> ResultEnvelope:
    if op.remind_at < ctx.now_ist:
        return ResultEnvelope(status="invalid", error="Cannot set reminder in the past.")
        
    warning = "Reminder is more than a year ahead." if (op.remind_at - ctx.now_ist).days > 365 else None
    
    party_id_val = int(op.party.party_id) if op.party and op.party.ref_type == "existing" else None

    # Check for duplicates on the same day for same party
    if party_id_val:
        dup_stmt = select(reminders.c.id).where(
            and_(
                reminders.c.business_id == ctx.business_id,
                reminders.c.party_id == party_id_val,
                reminders.c.status == "pending",
                func.date(reminders.c.remind_at) == op.remind_at.date()
            )
        )
        if (await ctx.conn.execute(dup_stmt)).fetchone():
            return ResultEnvelope(status="conflict", error="A reminder already exists for this party on this date.")

    stmt = insert(reminders).values(
        business_id=ctx.business_id,
        party_id=party_id_val,
        note=op.note,
        remind_at=op.remind_at,
        status="pending"
    ).returning(reminders.c.id)
    
    res = await ctx.conn.execute(stmt)
    return ResultEnvelope(status="ok", data={"reminder_id": str(res.scalar_one()), "warning": warning})

async def execute_reschedule_reminder(op: RescheduleReminderOp, ctx: ExecutionContext) -> ResultEnvelope:
    stmt = update(reminders).where(
        and_(
            reminders.c.id == int(op.reminder_id),
            reminders.c.business_id == ctx.business_id,
            reminders.c.status == "pending"
        )
    ).values(remind_at=op.remind_at).returning(reminders.c.id)
    
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
async def execute_resolve_date(op: ResolveDateOp, ctx: ExecutionContext) -> ResultEnvelope:
    # A lightweight python-side resolution. (In production, this could call an NLP date parser)
    exp = op.expression.lower().strip()
    resolved = None
    assumption = ""

    if exp in ("today", "aaj"):
        resolved = ctx.now_ist.date()
        assumption = "Today"
    elif exp in ("yesterday", "kal") and op.prefer.value == "past":
        resolved = (ctx.now_ist - timedelta(days=1)).date()
        assumption = "Yesterday"
    elif exp in ("tomorrow", "kal") and op.prefer.value == "future":
        resolved = (ctx.now_ist + timedelta(days=1)).date()
        assumption = "Tomorrow"
    else:
        # Fallback to today if not perfectly matched for MVP
        resolved = ctx.now_ist.date()
        assumption = f"Assuming today for '{exp}'"

    return ResultEnvelope(status="ok", data={"resolved_date": resolved.isoformat(), "assumption": assumption})

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
    
    # Half-up rounding to 2 decimals
    final_val = result_val.quantize(Decimal("0.01"))
    return ResultEnvelope(status="ok", data={"result": str(final_val)})

async def execute_ask_user(op: AskUserOp, ctx: ExecutionContext) -> ResultEnvelope:
    payload = op.model_dump(exclude_none=True)
    
    insert_stmt = insert(pending_actions).values(
        business_id=ctx.business_id,
        intent="ask_user",
        collected_fields=payload,
        awaiting=op.kind.value,
        expires_at=ctx.now_ist + timedelta(minutes=10)
    ).returning(pending_actions.c.id)
    
    res = await ctx.conn.execute(insert_stmt)
    return ResultEnvelope(status="needs_input", data={"action_id": str(res.scalar_one()), "ask_payload": payload})

async def execute_reply(op: ReplyOp, ctx: ExecutionContext) -> ResultEnvelope:
    # Signals the end of the plan with a template response
    return ResultEnvelope(status="ok", data={"template_id": op.template_id, "slots": op.slots})


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
