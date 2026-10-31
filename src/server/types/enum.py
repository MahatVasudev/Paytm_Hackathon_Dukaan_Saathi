from enum import Enum

class PartyTypeHint(str, Enum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    ANY = "any"

class PartyType(str, Enum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    BOTH = "both"

class DuesDirection(str, Enum):
    THEY_OWE_YOU = "they_owe_you"
    YOU_OWE_THEM = "you_owe_them"
    BOTH = "both"

class BalanceDirection(str, Enum):
    THEY_OWE_YOU = "they_owe_you"
    YOU_OWE_THEM = "you_owe_them"
    SETTLED = "settled"

class DuesSort(str, Enum):
    AMOUNT_DESC = "amount_desc"
    OLDEST_FIRST = "oldest_first"

class TransactionType(str, Enum):
    SALE = "sale"
    PURCHASE = "purchase"
    EXPENSE = "expense"
    PAYMENT_RECEIVED = "payment_received"
    PAYMENT_PAID = "payment_paid"

class TransactionStatusFilter(str, Enum):
    CONFIRMED = "confirmed"
    VOIDED = "voided"
    ANY = "any"

class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"

class AggregateMetric(str, Enum):
    # Accrual
    SALES_TOTAL = "sales_total"
    PURCHASES_TOTAL = "purchases_total"
    EXPENSES_TOTAL = "expenses_total"
    PAYMENTS_RECEIVED_TOTAL = "payments_received_total"
    PAYMENTS_PAID_TOTAL = "payments_paid_total"
    ENTRY_COUNT = "entry_count"
    AVG_ENTRY_SIZE = "avg_entry_size"
    # Cash
    CASH_IN = "cash_in"
    CASH_OUT = "cash_out"

class GroupByDimension(str, Enum):
    PARTY = "party"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    CATEGORY = "category"
    TYPE = "type"

class EntryScope(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    ANY = "any"

class ReminderStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    CANCELLED = "cancelled"

class PaymentStatus(str, Enum):
    PAID_IN_FULL = "paid_in_full"
    PARTIAL = "partial"
    UNPAID = "unpaid"
    UNKNOWN = "unknown"

class PaymentDirection(str, Enum):
    RECEIVED = "received"
    PAID = "paid"
    AUTO = "auto"

class DateResolveMode(str, Enum):
    DATE = "date"
    RANGE = "range"
    DATETIME = "datetime"

class DatePrefer(str, Enum):
    PAST = "past"
    FUTURE = "future"
    AUTO = "auto"

class CalcOp(str, Enum):
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    PERCENT_OF = "percent_of"
    PCT_CHANGE = "pct_change"
    MIN = "min"
    MAX = "max"

class AskKind(str, Enum):
    CHOOSE_PARTY = "choose_party"
    CHOOSE_OPTION = "choose_option"
    MISSING_FIELD = "missing_field"
    CONFIRM_ASSUMPTION = "confirm_assumption"

class ResultStatus(str, Enum):
    OK = "ok"
    NEEDS_INPUT = "needs_input"
    NOT_FOUND = "not_found"
    INVALID = "invalid"
    CONFLICT = "conflict"
    ERROR = "error"


