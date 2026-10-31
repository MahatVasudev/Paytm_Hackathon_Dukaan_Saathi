import json

db_schema_context = {
    "party_balances": {
        "type": "view",
        "description": "Calculates the net balance and debt direction between a business and a party.",
        "columns": {
            "business_id": "BIGINT",
            "party_id": "BIGINT",
            "balance": "BIGINT (Integer paise)"
        },
        "business_rules": [
            "balance > 0: The party owes the business.",
            "balance < 0: The business owes the party.",
            "Calculated dynamically by summing confirmed transaction amounts based on their type."
        ]
    },
    
    "businesses": {
        "type": "table",
        "description": "Core tenant table representing a shopkeeper's account.",
        "columns": {
            "id": "BIGINT PRIMARY KEY",
            "name": "TEXT",
            "channel": "TEXT (telegram, whatsapp)",
            "external_chat_id": "TEXT",
            "language_pref": "TEXT"
        },
        "business_rules": [
            "A business is uniquely identified by the combination of its channel and external_chat_id."
        ]
    },
    
    "parties": {
        "type": "table",
        "description": "Stores customers and suppliers linked to a specific business.",
        "columns": {
            "id": "BIGINT PRIMARY KEY",
            "business_id": "BIGINT",
            "name": "TEXT",
            "normalized_name": "TEXT",
            "phone": "TEXT",
            "type": "TEXT (customer, supplier, both)"
        },
        "relationships": [
            "Belongs to businesses(id) ON DELETE CASCADE."
        ],
        "business_rules": [
            "phone must follow E.164 format if provided.",
            "Enforces composite unique key (id, business_id) to strictly scope child records to the same business."
        ],
        "indexes": [
            "GIN trgm index on normalized_name for fuzzy text matching."
        ]
    },
    
    "party_aliases": {
        "type": "table",
        "description": "Stores alternate names or nicknames for parties to improve fuzzy matching.",
        "columns": {
            "party_id": "BIGINT",
            "alias": "TEXT",
            "normalized_alias": "TEXT"
        },
        "indexes": [
            "GIN trgm index on normalized_alias for fuzzy text matching."
        ]
    },
    
    "messages": {
        "type": "table",
        "description": "Logs all inbound and outbound chat messages and voice transcripts.",
        "columns": {
            "id": "BIGINT PRIMARY KEY",
            "business_id": "BIGINT",
            "direction": "TEXT (in, out)",
            "type": "TEXT (text, voice, button_tap, system)",
            "text": "TEXT",
            "transcript": "TEXT",
            "transcription_status": "TEXT",
            "external_message_id": "TEXT",
            "update_id": "BIGINT",
            "party_id": "BIGINT"
        },
        "relationships": [
            "Foreign key to parties (id, business_id) ensuring party belongs to the same business."
        ],
        "business_rules": [
            "Idempotency: Unique constraints on external_message_id and Telegram update_id prevent duplicate processing."
        ]
    },
    
    "media": {
        "type": "table",
        "description": "Tracks files like bills, receipts, or voice notes attached to messages.",
        "columns": {
            "id": "BIGINT PRIMARY KEY",
            "message_id": "BIGINT",
            "kind": "TEXT (audio, image, document)",
            "size_bytes": "BIGINT",
            "sha256": "TEXT",
            "storage_path": "TEXT"
        },
        "business_rules": [
            "size_bytes must be >= 0."
        ],
        "indexes": [
            "Indexed by sha256 to catch and prevent duplicate file uploads."
        ]
    },
    
    "transactions": {
        "type": "table",
        "description": "Core financial ledger for all money movements.",
        "columns": {
            "id": "BIGINT PRIMARY KEY",
            "business_id": "BIGINT",
            "party_id": "BIGINT (Nullable for cash sales/expenses)",
            "type": "TEXT (sale, purchase, expense, payment_received, payment_paid)",
            "total_amount": "BIGINT",
            "paid_amount": "BIGINT",
            "status": "TEXT (pending, confirmed, voided)",
            "source": "TEXT (text, voice)",
            "source_message_id": "BIGINT"
        },
        "relationships": [
            "Foreign key to parties (id, business_id)."
        ],
        "business_rules": [
            "All amounts are stored as strictly positive integer paise (total_amount > 0).",
            "paid_amount must be >= 0 and <= total_amount.",
            "payment_is_fully_paid: Payments cannot have pending balances.",
            "payment_needs_party: Payments must always be linked to a party.",
            "unpaid_needs_party: Any unpaid remainder (debt) must be linked to a party.",
            "expense_is_fully_paid: Expenses cannot be recorded on credit."
        ]
    },
    
    "pending_actions": {
        "type": "table",
        "description": "State machine tracking incomplete multi-step bot workflows.",
        "columns": {
            "id": "BIGINT PRIMARY KEY",
            "business_id": "BIGINT",
            "intent": "TEXT",
            "collected_fields": "JSONB",
            "awaiting": "TEXT",
            "status": "TEXT (open, completed, cancelled, expired)"
        },
        "business_rules": [
            "Unique index ensures only ONE 'open' action exists per business at a time, preventing competing workflows."
        ]
    },
    
    "agent_runs": {
        "type": "table",
        "description": "Audit log tracking the LLM's execution steps for debugging.",
        "columns": {
            "id": "BIGINT PRIMARY KEY",
            "message_id": "BIGINT",
            "step_no": "INTEGER",
            "operation": "TEXT",
            "params": "JSONB",
            "result_summary": "JSONB",
            "error": "TEXT"
        },
        "business_rules": [
            "Unique constraint on (message_id, step_no) to maintain execution order."
        ]
    },
    
    "reminders": {
        "type": "table",
        "description": "Stores scheduled alerts and payment reminders linked to specific parties.",
        "columns": {
            "id": "BIGINT PRIMARY KEY",
            "business_id": "BIGINT",
            "party_id": "BIGINT",
            "remind_at": "TIMESTAMPTZ",
            "status": "TEXT (pending, processing, sent, failed, cancelled)"
        },
        "business_rules": [
            "Financial amounts are deliberately NOT stored here. Balances are calculated dynamically when the reminder fires."
        ]
    }
}

with open("./schema_context.json", "w", encoding="utf-8") as file:
    json.dump(db_schema_context, file, indent=4)

print("Schema context with dynamic keys successfully saved to schema_context.json")
