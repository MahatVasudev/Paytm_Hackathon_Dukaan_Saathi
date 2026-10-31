from datetime import datetime

OUTPUT_TEMPLATES = {
    # ==========================================
    # 1. Balances & Dues
    # ==========================================
    "balance": (
        "👤 *{party_name}*\n"
        "💰 *Balance:* ₹{net_balance} ({direction_text})\n"
        "📝 *Open Entries:* {open_entry_count}\n"
        "📅 *Last Payment:* {last_payment_date}"
    ),
    "settled": (
        "✅ *{party_name}*\n"
        "Account is fully settled!\n"
        "📅 Last payment was on {last_payment_date}."
    ),
    "balance_opposite": (
        "👤 *{party_name}*\n"
        "{headline}\n"
        "↔️ {detail}\n"
        "📝 *Open Entries:* {open_entry_count}"
    ),
    "dues_list": (
        "📒 *Pending Dues* ({direction_text})\n"
        "💰 *Total:* ₹{total_amount} across {count} parties.\n\n"
        "{rows_text}\n\n"
        "_Showing {visible_count} of {count} records._"
    ),
    "no_dues": (
        "🎉 All clear! There are no pending dues matching this criteria."
    ),

    # ==========================================
    # 2. Writes & Approvals (Pending Actions)
    # ==========================================
    "pending_created": (
        "⏳ *Please Confirm*\n"
        "*Type:* {transaction_type}\n"
        "*Party:* {party_name}\n"
        "*Amount:* ₹{amount}\n"
        "*Status:* {payment_status}\n\n"
        "⚖️ *Resulting Balance:* ₹{resulting_balance} ({direction_text})\n\n"
        "_Tap Save to confirm, or Cancel._"
    ),
    "pending_created_simple": (
        "⏳ *Please Confirm*\n"
        "*Type:* {transaction_type}\n"
        "*For:* {party_name}\n"
        "*Amount:* ₹{amount}\n"
        "*Status:* {payment_status}\n\n"
        "_Tap Save to confirm, or Cancel._"
    ),

    # ==========================================
    # 3. Transactions & Ledgers
    # ==========================================
    "transaction_list": (
        "📄 *Recent Transactions*\n"
        "{party_name_header}\n\n"
        "{rows_text}\n\n"
        "_Reply with \"Next\" to see more._"
    ),
    "transaction_detail": (
        "🧾 *Transaction Details*\n"
        "*ID:* {transaction_id}\n"
        "*Date:* {date}\n"
        "*Party:* {party_name}\n"
        "*Type:* {type}\n"
        "*Total:* ₹{total}\n"
        "*Paid:* ₹{paid}\n"
        "*Note:* {note}\n"
        "*Status:* {status_icon} {status}"
    ),
    "no_results": (
        "🔍 No transactions found for this date range or filter."
    ),
    "none_found": (
        "🔍 No records found matching your request."
    ),

    # ==========================================
    # 4. Aggregates & Analytics
    # ==========================================
    "aggregate_table": (
        "📊 *Business Summary*\n"
        "*Metric:* {metric_name}\n\n"
        "{preview_rows_text}\n\n"
        "_{partial_period_warning}_"
    ),
    "no_activity": (
        "📉 No business activity recorded for this period."
    ),

    # ==========================================
    # 5. Party Resolution (Ambiguity Handling)
    # ==========================================
    "party_ambiguous": (
        "🤔 I found multiple matches for '*{queried_name}*'.\n"
        "Which one did you mean?\n\n"
        "{candidates_text}"
    ),
    "party_not_found": (
        "❌ I couldn't find a customer or supplier named '*{queried_name}*'.\n"
        "Check the spelling, or add them as a new party."
    ),

    # ==========================================
    # 6. Error & Flow States (GenericStateData)
    # ==========================================
    "needs_input": (
        "✍️ *Missing Information*\n"
        "{message}\n\n"
        "Please provide: {missing_fields_list}"
    ),
    "conflict": (
        "⚠️ *Action Required*\n"
        "{message}"
    ),
    "invalid": (
        "❌ *Error*\n"
        "{message}"
    ),
    "error": (
        "❌ *System Error*\n"
        "{message}"
    ),

    # ==========================================
    # 7. Reminders & Utilities
    # ==========================================
    "reminder_list": (
        "⏰ *Your Reminders*\n\n"
        "{rows_text}"
    ),
    "reminder_due": (
        "⏰ *Reminder*\n"
        "{note}"
    ),
    "reminder_due_party": (
        "⏰ *Reminder*\n"
        "{note}\n\n"
        "👤 *{party_name}:* ₹{balance} ({direction_text})"
    ),
    "reminder_set": (
        "⏰ *Reminder set* for {when}\n"
        "{note}"
    ),
    "date_resolved": (
        "📅 Understood as: *{assumption}* ({resolved_date})"
    ),
    "calc_result": (
        "🧮 *Result:* {value}"
    )
}

def esc(value) -> str:
    """Escape Telegram legacy-Markdown specials in dynamic values."""
    out = str(value)
    for ch in ("\\", "_", "*", "`", "["):
        out = out.replace(ch, "\\" + ch)
    return out


def render_template(kind: str, data: dict) -> str:
    """Fetches the template and injects the variables."""
    template = OUTPUT_TEMPLATES.get(kind, "✅ Processed successfully.")
    try:
        return template.format(**data)
    except KeyError as e:
        return f"Template rendering error: Missing key {e} in data dictionary."


# ======================================================================
# Template selection: pick the best-matching template for an executor result
# ======================================================================
def _dtext(direction: str) -> str:
    return {"they_owe_you": "they owe you", "you_owe_them": "you owe them"}.get(direction, "settled")


def _balance_view(d: dict) -> tuple[str, dict]:
    """Balance result -> 'settled' | 'balance_opposite' | 'balance', honouring which way the user asked."""
    name = esc(d.get("party_name") or "This party")
    amount, direction = d.get("balance", "0.00"), d.get("direction", "settled")
    persp = d.get("perspective", "both")
    last_pay = d.get("last_payment_date") or "—"
    entries = d.get("open_entry_count", 0)

    if direction == "settled":
        if persp == "you_owe_them":
            return "balance_opposite", dict(party_name=name, headline=f"💰 *You owe {name}:* ₹0.00",
                                            detail="All settled, nothing pending.",
                                            open_entry_count=entries)
        return "settled", dict(party_name=name, last_payment_date=esc(last_pay))

    # asked "how much do I owe X" but X owes the user (or the reverse)
    if persp in ("you_owe_them", "they_owe_you") and persp != direction:
        if persp == "you_owe_them":
            headline, detail = f"💰 *You owe {name}:* ₹0.00", f"{name} owes you ₹{esc(amount)} instead."
        else:
            headline, detail = f"💰 *{name} owes you:* ₹0.00", f"You owe {name} ₹{esc(amount)} instead."
        return "balance_opposite", dict(party_name=name, headline=headline, detail=detail, open_entry_count=entries)

    return "balance", dict(party_name=name, net_balance=esc(amount), direction_text=_dtext(direction),
                           open_entry_count=entries, last_payment_date=esc(last_pay))


def select_and_render(result: dict) -> str | None:
    """Return Telegram-Markdown text for an orchestrator 'template' / 'table_view' result,
    or None if no template fits (caller falls back to a generic message)."""
    op = result.get("op", "")
    d = result.get("data") or {}
    call = result.get("call") or {}

    # --- staged writes -------------------------------------------------
    if "action_id" in d:
        prev = d.get("preview")
        if not prev:
            return None
        slots = {k: esc(v) for k, v in prev.items()}
        text = render_template("pending_created" if "resulting_balance" in slots else "pending_created_simple", slots)
        if d.get("warning"):
            text += f"\n⚠️ _{esc(d['warning'])}_"
        return text

    # --- balances --------------------------------------------------------
    if op == "get_outstanding" and "direction" in d:
        kind, slots = _balance_view(d)
        return render_template(kind, slots)
    if op == "find_party" and len(d.get("candidates", [])) == 1:
        c = d["candidates"][0]
        kind, slots = _balance_view({"party_name": c["name"], "balance": c["balance"], "direction": c["direction"]})
        return render_template(kind, {**{"open_entry_count": "—", "last_payment_date": "—"}, **slots})

    # --- dues --------------------------------------------------------------
    if op == "list_all_dues":
        rows = d.get("rows", [])
        if not rows:
            return render_template("no_dues", {})
        total = sum(float(r["balance"]) for r in rows)
        rows_text = "\n".join(f"• {esc(r['name'])}: ₹{esc(r['balance'])} ({_dtext(r['direction'])})" for r in rows)
        return render_template("dues_list", dict(
            direction_text=_dtext(call.get("direction", "they_owe_you")), total_amount=f"{total:.2f}",
            count=len(rows), rows_text=rows_text, visible_count=len(rows)))

    # --- ledger ------------------------------------------------------------
    if op == "list_transactions":
        rows = d.get("rows", [])
        if not rows:
            return render_template("no_results", {})
        rows_text = "\n".join(
            f"• {esc(r['date'])} | {esc(str(r['type']).replace('_', ' ').upper())} | ₹{esc(r['total'])}"
            + (f" ({esc(r['note'])})" if r.get("note") else "") for r in rows)
        return render_template("transaction_list", dict(party_name_header=f"👤 *{esc(d['party_name'])}*" if d.get("party_name") else "_All parties_", rows_text=rows_text))
    if op == "get_transaction" and "transaction" in d:
        t = d["transaction"]
        icon = {"confirmed": "✅", "voided": "🗑"}.get(t.get("status"), "⏳")
        return render_template("transaction_detail", dict(
            transaction_id=esc(t["id"]), date=esc(t.get("transaction_date", "")), party_name=esc(t.get("party_id") or "—"),
            type=esc(str(t["type"]).replace("_", " ")), total=esc(t["total_amount"]), paid=esc(t["paid_amount"]),
            note=esc(t.get("note") or "—"), status_icon=icon, status=esc(t.get("status", ""))))

    # --- aggregates --------------------------------------------------------
    if op == "aggregate":
        rows = d.get("rows", [])
        if not rows or all(r.get("value") in (None, "0.00", 0) and len(r) == 1 for r in rows):
            return render_template("no_activity", {})
        lines = []
        for r in rows:
            dims = " | ".join(f"{esc(k[4:])}: {esc(v)}" for k, v in r.items() if k.startswith("dim_"))
            val = r.get("value")
            val_txt = esc(val) if call.get("metric") in ("entry_count",) else f"₹{esc(val)}"
            lines.append(f"• {dims + ' → ' if dims else ''}*{val_txt}*")
        return render_template("aggregate_table", dict(
            metric_name=esc(str(call.get("metric", "")).replace("_", " ")),
            preview_rows_text="\n".join(lines),
            partial_period_warning="Confirmed entries only"))

    # --- reminders ---------------------------------------------------------
    if op == "list_reminders":
        rem = d.get("reminders", [])
        if not rem:
            return render_template("none_found", {})
        return render_template("reminder_list", dict(
            rows_text="\n".join(f"• {esc(r['remind_at'][:16].replace('T', ' '))}: {esc(r.get('note') or '')}" for r in rem)))
    if op == "create_reminder" and "reminder_id" in d:
        when = "the set time"
        if d.get("remind_at"):
            dt = datetime.fromisoformat(d["remind_at"])
            soon = (dt - datetime.now(dt.tzinfo)).total_seconds() < 600
            when = dt.strftime("%I:%M:%S %p" if soon else "%d %b, %I:%M %p").lstrip("0")
        return render_template("reminder_set", {"when": esc(when), "note": esc(d.get("note") or "")}) + \
            (f"\n⚠️ _{esc(d['warning'])}_" if d.get("warning") else "")
    if op == "resolve_date" and "resolved_date" in d:
        return render_template("date_resolved", dict(assumption=esc(d["assumption"]), resolved_date=esc(d["resolved_date"])))
    if op == "calculate" and "result" in d:
        return render_template("calc_result", dict(value=esc(d["result"])))

    # --- "nothing found" shapes -------------------------------------------
    msg = d.get("message")
    if msg in ("no_activity",):
        return render_template("no_activity", {})
    if msg in ("none_found", "none", "no_results"):
        return render_template("none_found", {})
    return None
