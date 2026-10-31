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
        "_Tap Yes to save, or Edit to make changes._"
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
    "date_resolved": (
        "📅 Understood as: *{assumption}* ({resolved_date})"
    ),
    "calc_result": (
        "🧮 *Result:* {value}"
    )
}

def render_template(kind: str, data: dict) -> str:
    """Fetches the template and injects the variables."""
    template = OUTPUT_TEMPLATES.get(kind, "✅ Processed successfully.")
    try:
        # **data unpacks the dictionary to fill the {variables}
        return template.format(**data) 
    except KeyError as e:
        return f"Template rendering error: Missing key {e} in data dictionary."