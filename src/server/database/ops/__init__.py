from src.server.database.ops.utils import ResultEnvelope, ExecutionContext, to_rupees, to_paise
from src.server.types.pydantic import GetOutstandingOp, ProposePaymentOp, PaymentDirection, AggregateOp, AggregateMetric, GroupByDimension, SortOrder
from src.server.database.sqltypes import party_balances, pending_actions, parties, transactions
from sqlalchemy import select, and_,insert, func, desc, asc
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



