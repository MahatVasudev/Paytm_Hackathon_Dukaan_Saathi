import json

sql_registry = {
    "find_party": {
        "category": "lookup",
        "model": "FindPartyOp",
        "description": "Searches for a customer or supplier by name using PostgreSQL trigram similarity and fetches their current net balance.",
        "parameters": {
            "name": "RefOr[str] (Required)",
            "type_hint": "customer | supplier | any (Default: 'any')",
            "limit": "int (1 to 20, Default: 5)"
        },
        "tables_accessed": {
            "read": ["parties", "party_balances"],
            "write": []
        },
        "execution_rules": [
            "Normalizes the search string to lowercase and trims whitespace.",
            "Outer-joins 'parties' with 'party_balances' on business_id and party_id.",
            "Filters out candidates with a similarity score below 0.3.",
            "Returns status 'party_found' if exactly 1 match is found, 'party_ambiguous' if multiple matches exist, or 'not_found' if none."
        ],
        "returns": {
            "statuses": ["party_found", "party_ambiguous", "not_found"],
            "payload_fields": ["candidates (party_id, name, phone_last4, balance, direction, score)"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "Always use as the first step whenever a user mentions a customer or supplier by name to resolve their party_id before checking balances or recording transactions.",
                "user_says": "Ramesh General Store ka khata dekho",
                "step_call": {
                    "bind": "party_ramesh",
                    "call": {
                        "op": "find_party",
                        "name": "Ramesh General Store",
                        "type_hint": "customer",
                        "limit": 5
                    }
                }
            }
        ]
    },

    "get_outstanding": {
        "category": "read",
        "model": "GetOutstandingOp",
        "description": "Retrieves the net outstanding balance and debt direction for a specific existing party.",
        "parameters": {
            "party": "RefOr[PartyRefExisting] (Required)",
            "include_breakdown": "bool (Default: False)"
        },
        "tables_accessed": {
            "read": ["party_balances"],
            "write": []
        },
        "execution_rules": [
            "Queries 'party_balances' scoped by business_id and party_id.",
            "Converts integer paise to positive Rupee strings.",
            "Determines direction: 'they_owe_you' (balance > 0), 'you_owe_them' (balance < 0), or 'settled' (no balance)."
        ],
        "returns": {
            "statuses": ["ok"],
            "payload_fields": ["balance", "direction"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone asks how much a specific person owes or what their balance is, use this right after 'find_party'.",
                "user_says": "Ramesh ka kitna baaki hai?",
                "step_call": {
                    "bind": "ramesh_bal",
                    "call": {
                        "op": "get_outstanding",
                        "party": {
                            "ref_type": "existing",
                            "party_id": "$party_ramesh.party_id"
                        },
                        "include_breakdown": False
                    }
                }
            }
        ]
    },

    "list_all_dues": {
        "category": "read",
        "model": "ListAllDuesOp",
        "description": "Lists all non-zero party balances owed to or by the business.",
        "parameters": {
            "direction": "they_owe_you | you_owe_them | both (Default: 'they_owe_you')",
            "min_amount": "Optional[RefOr[Rupees]]",
            "older_than_days": "Optional[int] (1 to 3660)",
            "sort": "amount_desc | oldest_first (Default: 'amount_desc')",
            "limit": "int (1 to 50, Default: 10)"
        },
        "tables_accessed": {
            "read": ["party_balances", "parties"],
            "write": []
        },
        "execution_rules": [
            "Joins 'party_balances' and 'parties' where balance != 0.",
            "Filters by sign of balance based on 'direction'.",
            "Orders by absolute balance descending when sort is 'amount_desc'."
        ],
        "returns": {
            "statuses": ["ok"],
            "payload_fields": ["rows (party_id, name, balance, direction)", "message ('no_dues' if empty)"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone asks who all owe money to the shop ('market mein kitna udhaar hai') or which suppliers need to be paid.",
                "user_says": "Kis kis se paisa lena baaki hai? Top 10 dikhao.",
                "step_call": {
                    "bind": "all_receivables",
                    "call": {
                        "op": "list_all_dues",
                        "direction": "they_owe_you",
                        "sort": "amount_desc",
                        "limit": 10
                    }
                }
            },
            {
                "when_to_use": "If someone asks who the shop owes money to.",
                "user_says": "Suppliers ko kitna dena hai?",
                "step_call": {
                    "bind": "all_payables",
                    "call": {
                        "op": "list_all_dues",
                        "direction": "you_owe_them",
                        "sort": "amount_desc",
                        "limit": 10
                    }
                }
            }
        ]
    },

    "list_transactions": {
        "category": "read",
        "model": "ListTransactionsOp",
        "description": "Queries the financial ledger with optional filters for party, transaction type, date range, amount range, and note text.",
        "parameters": {
            "party": "Optional[RefOr[PartyRefExisting]]",
            "types": "Optional[List[sale | purchase | expense | payment_received | payment_paid]]",
            "status": "confirmed | voided | any (Default: 'confirmed')",
            "from": "Optional[RefOr[IstDate]]",
            "to": "Optional[RefOr[IstDate]]",
            "text_contains": "Optional[ShortStr]",
            "min_amount": "Optional[RefOr[Rupees]]",
            "max_amount": "Optional[RefOr[Rupees]]",
            "order": "asc | desc (Default: 'desc')",
            "limit": "int (1 to 50, Default: 10)",
            "offset": "int (Default: 0)"
        },
        "tables_accessed": {
            "read": ["transactions"],
            "write": []
        },
        "execution_rules": [
            "Date range ('from' to 'to') cannot exceed 366 days.",
            "Uses case-insensitive ILIKE matching on the 'note' column if 'text_contains' is provided.",
            "Converts min_amount and max_amount to paise for filtering, and converts results back to Rupees."
        ],
        "returns": {
            "statuses": ["ok"],
            "payload_fields": ["rows (id, type, total, paid, date, note)", "message ('no_results' if empty)"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone asks to see recent entries, bill history for a customer, or searches for an item like 'sugar' in past bills.",
                "user_says": "Aaj ke saare sales dikhao",
                "step_call": {
                    "bind": "today_sales_list",
                    "call": {
                        "op": "list_transactions",
                        "types": ["sale"],
                        "status": "confirmed",
                        "from": "$today_date.resolved_date",
                        "to": "$today_date.resolved_date",
                        "order": "desc",
                        "limit": 10
                    }
                }
            }
        ]
    },

    "get_transaction": {
        "category": "read",
        "model": "GetTransactionOp",
        "description": "Fetches full details of a single transaction by its ID.",
        "parameters": {
            "transaction_id": "RefOr[str] (Required)"
        },
        "tables_accessed": {
            "read": ["transactions"],
            "write": []
        },
        "execution_rules": [
            "Enforces tenant isolation via business_id.",
            "Converts total_amount and paid_amount from paise to Rupee strings."
        ],
        "returns": {
            "statuses": ["ok", "not_found"],
            "payload_fields": ["transaction"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone asks for full details of a specific entry ID or references a transaction fetched from 'get_last_entry'.",
                "user_says": "Transaction #45 ki poori detail batao",
                "step_call": {
                    "bind": "tx_detail",
                    "call": {
                        "op": "get_transaction",
                        "transaction_id": "45"
                    }
                }
            }
        ]
    },

    "aggregate": {
        "category": "read",
        "model": "AggregateOp",
        "description": "Computes financial summaries, totals, counts, or averages over a date range, optionally grouped by up to 2 dimensions.",
        "parameters": {
            "metric": "sales_total | purchases_total | expenses_total | payments_received_total | payments_paid_total | entry_count | avg_entry_size | cash_in | cash_out (Required)",
            "group_by": "List[party | day | week | month | category | type] (Max 2, Default: [])",
            "party": "Optional[RefOr[PartyRefExisting]]",
            "category": "Optional[ShortStr]",
            "from": "RefOr[IstDate] (Required)",
            "to": "RefOr[IstDate] (Required)",
            "order": "asc | desc (Default: 'desc')",
            "limit": "int (1 to 50, Default: 10)"
        },
        "tables_accessed": {
            "read": ["transactions"],
            "write": []
        },
        "execution_rules": [
            "Only aggregates 'confirmed' transactions within a maximum 366-day window.",
            "'cash_in' sums paid_amount for sales and payment_received; 'cash_out' sums paid_amount for purchases, expenses, and payment_paid.",
            "Uses date_trunc for temporal grouping and coalesces null categories to 'uncategorized'."
        ],
        "returns": {
            "statuses": ["ok", "not_found"],
            "payload_fields": ["rows (value, dim_<dimension>)"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone asks for total sales, total expenses, cash in hand, or category-wise breakdown over a time period.",
                "user_says": "Is mahine total kitni bikri hui?",
                "step_call": {
                    "bind": "month_sales",
                    "call": {
                        "op": "aggregate",
                        "metric": "sales_total",
                        "group_by": [],
                        "from": "$this_month.resolved_from",
                        "to": "$this_month.resolved_to",
                        "order": "desc",
                        "limit": 10
                    }
                }
            }
        ]
    },

    "get_last_entry": {
        "category": "read",
        "model": "GetLastEntryOp",
        "description": "Finds the most recent pending action or confirmed transaction, used for context references like 'edit that' or 'delete last entry'.",
        "parameters": {
            "scope": "pending | confirmed | any (Default: 'any')",
            "party": "Optional[RefOr[PartyRefExisting]]",
            "type": "Optional[TransactionType]",
            "within_hours": "Optional[int] (1 to 720)"
        },
        "tables_accessed": {
            "read": ["pending_actions", "transactions"],
            "write": []
        },
        "execution_rules": [
            "Checks 'pending_actions' (where status='open') first if scope is 'any' or 'pending'.",
            "Falls back to 'transactions' (where status='confirmed') ordered by created_at DESC.",
            "Flags 'is_older_than_24h' if the confirmed transaction was created more than 24 hours ago."
        ],
        "returns": {
            "statuses": ["ok", "not_found"],
            "payload_fields": ["source ('pending' | 'confirmed')", "id", "intent / type", "is_older_than_24h"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone says 'pichli entry hatao' (delete last entry) or 'last bill mein galti hai' without giving a transaction ID.",
                "user_says": "Abhi jo bill daala usko cancel karo",
                "step_call": {
                    "bind": "last_tx",
                    "call": {
                        "op": "get_last_entry",
                        "scope": "confirmed",
                        "within_hours": 24
                    }
                }
            }
        ]
    },

    "list_reminders": {
        "category": "read",
        "model": "ListRemindersOp",
        "description": "Lists scheduled reminders filtered by status and optional party.",
        "parameters": {
            "status": "pending | sent | cancelled (Default: 'pending')",
            "party": "Optional[RefOr[PartyRefExisting]]",
            "limit": "int (1 to 50, Default: 10)"
        },
        "tables_accessed": {
            "read": ["reminders"],
            "write": []
        },
        "execution_rules": [
            "Queries 'reminders' ordered chronologically by remind_at."
        ],
        "returns": {
            "statuses": ["ok", "not_found"],
            "payload_fields": ["reminders (id, remind_at, note)"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone asks what reminders are currently set or upcoming.",
                "user_says": "Konse reminders lage hue hain?",
                "step_call": {
                    "bind": "active_reminders",
                    "call": {
                        "op": "list_reminders",
                        "status": "pending",
                        "limit": 10
                    }
                }
            }
        ]
    },

    "propose_sale": {
        "category": "write_pending",
        "model": "ProposeSaleOp",
        "description": "Stages a new customer sale in pending_actions awaiting user confirmation.",
        "parameters": {
            "party": "RefOr[PartyRef] (Required: existing, new, or none)",
            "total": "RefOr[Rupees] (Required)",
            "payment_status": "paid_in_full | partial | unpaid | unknown (Required)",
            "paid_amount": "Optional[RefOr[Rupees]] (Required if partial, forbidden otherwise)",
            "item_text": "Optional[NoteStr]",
            "date": "Optional[RefOr[IstDate]]",
            "note": "Optional[NoteStr]",
            "category": "Optional[ShortStr]"
        },
        "tables_accessed": {
            "read": [],
            "write": ["pending_actions"]
        },
        "execution_rules": [
            "Party cannot be 'none' if payment_status is 'unpaid' or 'partial' (unpaid debt requires a party).",
            "If payment_status is 'unknown', returns status='needs_input' prompting for Paid, Udhaar, or Part-paid.",
            "Stages payload in 'pending_actions' with intent='propose_sale', awaiting='confirmation', and a 10-minute expiry."
        ],
        "returns": {
            "statuses": ["ok", "needs_input"],
            "payload_fields": ["action_id"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone sells goods to a customer (on cash, credit/udhaar, or partial payment).",
                "user_says": "Ramesh ko 1200 ka saaman becha, 500 cash diya baaki udhaar",
                "step_call": {
                    "bind": "sale_stage",
                    "call": {
                        "op": "propose_sale",
                        "party": {
                            "ref_type": "existing",
                            "party_id": "$party_ramesh.party_id"
                        },
                        "total": "1200.00",
                        "payment_status": "partial",
                        "paid_amount": "500.00",
                        "item_text": "General goods"
                    }
                }
            },
            {
                "when_to_use": "If someone records an anonymous cash counter sale without naming a customer.",
                "user_says": "250 rupay ki nagad bikri hui",
                "step_call": {
                    "bind": "cash_sale",
                    "call": {
                        "op": "propose_sale",
                        "party": {
                            "ref_type": "none"
                        },
                        "total": "250.00",
                        "payment_status": "paid_in_full"
                    }
                }
            }
        ]
    },

    "propose_purchase": {
        "category": "write_pending",
        "model": "ProposePurchaseOp",
        "description": "Stages a new supplier purchase in pending_actions awaiting user confirmation.",
        "parameters": {
            "party": "RefOr[PartyRef] (Required: existing, new, or none)",
            "total": "RefOr[Rupees] (Required)",
            "payment_status": "paid_in_full | partial | unpaid | unknown (Required)",
            "paid_amount": "Optional[RefOr[Rupees]] (Required if partial, forbidden otherwise)",
            "item_text": "Optional[NoteStr]",
            "date": "Optional[RefOr[IstDate]]",
            "note": "Optional[NoteStr]",
            "category": "Optional[ShortStr]"
        },
        "tables_accessed": {
            "read": [],
            "write": ["pending_actions"]
        },
        "execution_rules": [
            "Party cannot be 'none' if payment_status is 'unpaid' or 'partial'.",
            "If payment_status is 'unknown', returns status='needs_input'.",
            "Stages payload in 'pending_actions' with intent='propose_purchase' and a 10-minute expiry."
        ],
        "returns": {
            "statuses": ["ok", "needs_input"],
            "payload_fields": ["action_id"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If the shopkeeper buys stock/inventory from a wholesaler or supplier (or uploads a supplier tax invoice).",
                "user_says": "Sharma Traders se 5439 ka maal kharida udhaar pe",
                "step_call": {
                    "bind": "purchase_stage",
                    "call": {
                        "op": "propose_purchase",
                        "party": {
                            "ref_type": "existing",
                            "party_id": "$party_sharma.party_id"
                        },
                        "total": "5439.00",
                        "payment_status": "unpaid",
                        "item_text": "Stock purchase"
                    }
                }
            }
        ]
    },

    "propose_expense": {
        "category": "write_pending",
        "model": "ProposeExpenseOp",
        "description": "Stages a business expense in pending_actions. Always fully paid and never linked to a party.",
        "parameters": {
            "amount": "RefOr[Rupees] (Required)",
            "category": "ShortStr (Required)",
            "date": "Optional[RefOr[IstDate]]",
            "note": "Optional[NoteStr]"
        },
        "tables_accessed": {
            "read": [],
            "write": ["pending_actions"]
        },
        "execution_rules": [
            "Sets both resolved_total_paise and resolved_paid_paise to the expense amount.",
            "Stages payload in 'pending_actions' with intent='propose_expense' and a 10-minute expiry."
        ],
        "returns": {
            "statuses": ["ok"],
            "payload_fields": ["action_id"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If the shopkeeper spends money on shop overheads like tea, electricity bill, transport, rent, or salaries.",
                "user_says": "Dukaan ka bijli bill 1800 bhar diya",
                "step_call": {
                    "bind": "expense_stage",
                    "call": {
                        "op": "propose_expense",
                        "amount": "1800.00",
                        "category": "electricity",
                        "note": "Dukaan bijli bill"
                    }
                }
            }
        ]
    },

    "propose_payment": {
        "category": "write_pending",
        "model": "ProposePaymentOp",
        "description": "Stages a payment received from or paid to a party, automatically inferring direction from current balances if set to 'auto'.",
        "parameters": {
            "party": "RefOr[PartyRefExisting | PartyRefNew] (Required)",
            "amount": "Optional[RefOr[Rupees]] (XOR with settle_all)",
            "settle_all": "Optional[Literal[True]] (XOR with amount)",
            "direction": "received | paid | auto (Default: 'auto')",
            "date": "Optional[RefOr[IstDate]]",
            "note": "Optional[NoteStr]",
            "category": "Optional[ShortStr]"
        },
        "tables_accessed": {
            "read": ["party_balances"],
            "write": ["pending_actions"]
        },
        "execution_rules": [
            "Looks up current balance from 'party_balances' if the party is 'existing'.",
            "If 'settle_all' is True, uses the absolute current balance (returns 'invalid' if balance is already settled).",
            "If direction is 'auto', resolves to 'received' when balance > 0, 'paid' when balance < 0, or returns 'needs_input' if settled.",
            "Generates a warning if the payment amount exceeds the current outstanding balance.",
            "Stages payload in 'pending_actions' with intent='propose_payment'."
        ],
        "returns": {
            "statuses": ["ok", "needs_input", "invalid"],
            "payload_fields": ["action_id", "warning"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If a customer pays back their udhaar or the shopkeeper pays a supplier.",
                "user_says": "Ramesh ne 500 rupay GPay kiye",
                "step_call": {
                    "bind": "payment_stage",
                    "call": {
                        "op": "propose_payment",
                        "party": {
                            "ref_type": "existing",
                            "party_id": "$party_ramesh.party_id"
                        },
                        "amount": "500.00",
                        "direction": "received",
                        "note": "GPay"
                    }
                }
            },
            {
                "when_to_use": "If someone says a party cleared their entire account ('hisaab chukta kar diya') without stating the exact amount.",
                "user_says": "Ramesh ne poora hisaab clear kar diya",
                "step_call": {
                    "bind": "settle_stage",
                    "call": {
                        "op": "propose_payment",
                        "party": {
                            "ref_type": "existing",
                            "party_id": "$party_ramesh.party_id"
                        },
                        "settle_all": True,
                        "direction": "auto"
                    }
                }
            }
        ]
    },

    "propose_void": {
        "category": "write_pending",
        "model": "ProposeVoidOp",
        "description": "Stages a request to void an existing confirmed transaction.",
        "parameters": {
            "transaction_id": "RefOr[str] (Required)",
            "reason": "Optional[NoteStr]"
        },
        "tables_accessed": {
            "read": ["transactions"],
            "write": ["pending_actions"]
        },
        "execution_rules": [
            "Verifies the transaction exists in 'transactions' and is not already 'voided'.",
            "Attaches a warning if the transaction is older than 30 days.",
            "Stages payload in 'pending_actions' with intent='propose_void'."
        ],
        "returns": {
            "statuses": ["ok", "not_found", "invalid"],
            "payload_fields": ["action_id", "warning"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone wants to delete or cancel a confirmed transaction (usually chained after 'get_last_entry').",
                "user_says": "Pichla bill galti se dal gaya, usko delete karo",
                "step_call": {
                    "bind": "void_stage",
                    "call": {
                        "op": "propose_void",
                        "transaction_id": "$last_tx.id",
                        "reason": "Entered by mistake"
                    }
                }
            }
        ]
    },

    "propose_correction": {
        "category": "write_pending",
        "model": "ProposeCorrectionOp",
        "description": "Stages a correction (void and replace) for an existing confirmed transaction.",
        "parameters": {
            "transaction_id": "RefOr[str] (Required)",
            "changes": "EntryChanges (At least one of: party, total, paid_amount, payment_status, date, note, category)"
        },
        "tables_accessed": {
            "read": ["transactions"],
            "write": ["pending_actions"]
        },
        "execution_rules": [
            "Verifies the target transaction exists and is not voided.",
            "Stages payload in 'pending_actions' with intent='propose_correction'."
        ],
        "returns": {
            "statuses": ["ok", "invalid"],
            "payload_fields": ["action_id"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone wants to change the amount or details of an already confirmed transaction.",
                "user_says": "Pichle bill mein amount 500 nahi 550 tha, theek karo",
                "step_call": {
                    "bind": "correction_stage",
                    "call": {
                        "op": "propose_correction",
                        "transaction_id": "$last_tx.id",
                        "changes": {
                            "total": "550.00"
                        }
                    }
                }
            }
        ]
    },

    "edit_pending": {
        "category": "write_pending",
        "model": "EditPendingOp",
        "description": "Modifies fields on a currently open pending action before it is confirmed.",
        "parameters": {
            "item_index": "Optional[int] (>= 0)",
            "changes": "EntryChanges (At least one of: party, total, paid_amount, payment_status, date, note, category)"
        },
        "tables_accessed": {
            "read": ["pending_actions"],
            "write": ["pending_actions"]
        },
        "execution_rules": [
            "Updates collected_fields in the active open row in 'pending_actions'."
        ],
        "returns": {
            "statuses": ["ok", "not_found"],
            "payload_fields": ["action_id"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "When the bot is waiting for confirmation on a staged entry and the user replies with a correction like 'make it 600 instead' or 'mark it unpaid'.",
                "user_says": "Nahi, 400 nahi 450 karo aur udhaar likho",
                "step_call": {
                    "bind": "edit_open_pending",
                    "call": {
                        "op": "edit_pending",
                        "changes": {
                            "total": "450.00",
                            "payment_status": "unpaid"
                        }
                    }
                }
            }
        ]
    },

    "create_reminder": {
        "category": "reminder",
        "model": "CreateReminderOp",
        "description": "Creates a scheduled reminder for a future date/time, optionally linked to a party.",
        "parameters": {
            "party": "Optional[RefOr[PartyRefExisting]]",
            "remind_at": "RefOr[IstDateTime] (Required)",
            "note": "NoteStr (Required)"
        },
        "tables_accessed": {
            "read": ["reminders"],
            "write": ["reminders"]
        },
        "execution_rules": [
            "Rejects reminders set in the past ('invalid').",
            "Checks for duplicate pending reminders on the same date for the same party ('conflict').",
            "Warns if the reminder is scheduled more than 365 days in the future."
        ],
        "returns": {
            "statuses": ["ok", "invalid", "conflict"],
            "payload_fields": ["reminder_id", "warning"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone asks to be reminded to collect payment from a customer or pay a supplier on a future date.",
                "user_says": "Kal subah 10 baje Ramesh se paise maangne ka reminder laga do",
                "step_call": {
                    "bind": "new_reminder",
                    "call": {
                        "op": "create_reminder",
                        "party": {
                            "ref_type": "existing",
                            "party_id": "$party_ramesh.party_id"
                        },
                        "remind_at": "$tomorrow_10am.resolved_datetime",
                        "note": "Ramesh se payment lena hai"
                    }
                }
            }
        ]
    },

    "reschedule_reminder": {
        "category": "reminder",
        "model": "RescheduleReminderOp",
        "description": "Updates the trigger timestamp of an existing pending reminder.",
        "parameters": {
            "reminder_id": "RefOr[str] (Required)",
            "remind_at": "RefOr[IstDateTime] (Required)"
        },
        "tables_accessed": {
            "read": [],
            "write": ["reminders"]
        },
        "execution_rules": [
            "Updates 'remind_at' only if the reminder belongs to the business and has status='pending'."
        ],
        "returns": {
            "statuses": ["ok", "invalid"],
            "payload_fields": ["status ('rescheduled')"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone wants to postpone or change the time of an existing reminder.",
                "user_says": "Reminder #12 ko parso subah 11 baje pe shift kar do",
                "step_call": {
                    "bind": "moved_reminder",
                    "call": {
                        "op": "reschedule_reminder",
                        "reminder_id": "12",
                        "remind_at": "$day_after_11am.resolved_datetime"
                    }
                }
            }
        ]
    },

    "cancel_reminder": {
        "category": "reminder",
        "model": "CancelReminderOp",
        "description": "Cancels pending reminders either by specific reminder_id or by party.",
        "parameters": {
            "reminder_id": "Optional[RefOr[str]] (XOR with party)",
            "party": "Optional[RefOr[PartyRefExisting]] (XOR with reminder_id)",
            "all_for_party": "bool (Default: False)"
        },
        "tables_accessed": {
            "read": [],
            "write": ["reminders"]
        },
        "execution_rules": [
            "Updates status to 'cancelled' on matching pending reminders."
        ],
        "returns": {
            "statuses": ["ok", "not_found"],
            "payload_fields": ["cancelled_count"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If someone wants to delete or stop reminders for a specific customer.",
                "user_says": "Ramesh ke saare reminders hata do",
                "step_call": {
                    "bind": "cancel_rem",
                    "call": {
                        "op": "cancel_reminder",
                        "party": {
                            "ref_type": "existing",
                            "party_id": "$party_ramesh.party_id"
                        },
                        "all_for_party": True
                    }
                }
            }
        ]
    },

    "resolve_date": {
        "category": "utility",
        "model": "ResolveDateOp",
        "description": "Converts relative natural language time expressions (e.g., 'aaj', 'kal', 'yesterday') into concrete IST dates.",
        "parameters": {
            "expression": "ShortStr (Required)",
            "mode": "date | range | datetime (Required)",
            "prefer": "past | future | auto (Default: 'auto')"
        },
        "tables_accessed": {
            "read": [],
            "write": []
        },
        "execution_rules": [
            "Evaluates expression relative to the execution context's current IST timestamp (ctx.now_ist)."
        ],
        "returns": {
            "statuses": ["ok"],
            "payload_fields": ["resolved_date", "assumption"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "Always call this first whenever the user uses relative words like 'kal', 'aaj', 'is mahine', or 'last week' instead of guessing the date yourself.",
                "user_says": "Kal kitna kharcha hua?",
                "step_call": {
                    "bind": "yesterday_date",
                    "call": {
                        "op": "resolve_date",
                        "expression": "kal",
                        "mode": "date",
                        "prefer": "past"
                    }
                }
            }
        ]
    },

    "calculate": {
        "category": "utility",
        "model": "CalculateOp",
        "description": "Performs exact Decimal arithmetic on numbers or earlier step variables.",
        "parameters": {
            "op_type": "add | subtract | multiply | divide | percent_of | pct_change | min | max (Required)",
            "operands": "List[Rupees | Decimal | int | VarRef] (Min length: 2)"
        },
        "tables_accessed": {
            "read": [],
            "write": []
        },
        "execution_rules": [
            "Rejects division by zero with status='invalid'.",
            "Quantizes results to 2 decimal places."
        ],
        "returns": {
            "statuses": ["ok", "invalid"],
            "payload_fields": ["result"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If you need to compute net profit (sales minus expenses), item totals (qty * rate), or GST percentages before passing the result to another step.",
                "user_says": "Is mahine ki bikri mein se kharcha nikaal ke kitna bacha?",
                "step_call": {
                    "bind": "net_savings",
                    "call": {
                        "op": "calculate",
                        "op_type": "subtract",
                        "operands": ["$month_sales.value", "$month_expenses.value"]
                    }
                }
            }
        ]
    },

    "ask_user": {
        "category": "control",
        "model": "AskUserOp",
        "description": "Pauses execution and stages a question in pending_actions when ambiguity or missing fields prevent proceeding.",
        "parameters": {
            "kind": "choose_party | choose_option | missing_field | confirm_assumption (Required)",
            "field": "ShortStr (Required)",
            "options": "List[AskOption(id, label)] (Max 10)",
            "slots": "Dict[str, VarRef | str]"
        },
        "tables_accessed": {
            "read": [],
            "write": ["pending_actions"]
        },
        "execution_rules": [
            "Inserts an open record into 'pending_actions' with intent='ask_user' and awaiting=op.kind."
        ],
        "returns": {
            "statuses": ["needs_input"],
            "payload_fields": ["action_id", "ask_payload"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "If the user gives an incomplete transaction (like missing the amount or not specifying whether a sale was cash or udhaar).",
                "user_says": "Ramesh ko saaman becha",
                "step_call": {
                    "bind": "ask_missing_total",
                    "call": {
                        "op": "ask_user",
                        "kind": "missing_field",
                        "field": "total",
                        "options": [],
                        "slots": {
                            "party_name": "Ramesh"
                        }
                    }
                }
            }
        ]
    },

    "reply": {
        "category": "control",
        "model": "ReplyOp",
        "description": "Concludes the execution plan by binding step output variables into a response template.",
        "parameters": {
            "template_id": "ShortStr (Required)",
            "slots": "Dict[str, VarRef | str] (Required)"
        },
        "tables_accessed": {
            "read": [],
            "write": []
        },
        "execution_rules": [
            "Strictly forbids raw numeric literals or currency strings inside 'slots'; all numbers must be passed via $var references."
        ],
        "returns": {
            "statuses": ["ok"],
            "payload_fields": ["template_id", "slots"]
        },
        "few_shot_examples": [
            {
                "when_to_use": "Use as the final step of a read or calculation plan to format the output template using '$variable' references.",
                "user_says": "Ramesh ka balance batao",
                "step_call": {
                    "bind": "final_msg",
                    "call": {
                        "op": "reply",
                        "template_id": "balance",
                        "slots": {
                            "party_name": "$party_ramesh.name",
                            "net_balance": "$ramesh_bal.balance",
                            "direction_text": "$ramesh_bal.direction"
                        }
                    }
                }
            }
        ]
    }
}

with open("sql_registry.json", "w", encoding="utf-8") as file:
    json.dump(sql_registry, file, indent=4, ensure_ascii=False)

print("SQL query registry with few-shot examples successfully saved to sql_registry.json")  