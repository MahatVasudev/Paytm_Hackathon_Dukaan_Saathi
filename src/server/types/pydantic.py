from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, TypeVar, Union
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    condecimal,
    constr,
    field_validator,
    model_validator,
)

# =====================================================================
# 1. BASE CONFIGURATION & PRIMITIVES
# =====================================================================

class StrictModel(BaseModel):
    """Base model forbidding any extra hallucinated parameters from the LLM."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# Variable Reference: Matches "$step1", "$ramesh.id", etc.
VarRef = Annotated[
    str,
    Field(
        pattern=r"^\$[a-zA-Z_][a-zA-Z0-9_.]*$",
        description="Reference to an earlier step's result variable (e.g., '$party_1')",
    ),
]

T = TypeVar("T")
RefOr = Union[T, VarRef]

# Money: Strictly > 0, max 2 decimal places. Executor converts to integer paise.
Rupees = Annotated[
    Decimal,
    condecimal(gt=Decimal("0"), decimal_places=2),
]

# Dates & Strings
IstDate = date
IstDateTime = datetime
NoteStr = Annotated[str, constr(strip_whitespace=True, max_length=200)]
ShortStr = Annotated[str, constr(strip_whitespace=True, min_length=1, max_length=100)]


# =====================================================================
# 2. ENUMS
# =====================================================================

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


# =====================================================================
# 3. DISCRIMINATED UNION: PartyRef
# =====================================================================

class PartyRefExisting(StrictModel):
    ref_type: Literal["existing"] = "existing"
    party_id: RefOr[str]

class PartyRefNew(StrictModel):
    ref_type: Literal["new"] = "new"
    name: ShortStr
    phone: Optional[Annotated[str, constr(pattern=r"^\+?[0-9]{10,13}$")]] = None
    type: Optional[PartyType] = None

class PartyRefNone(StrictModel):
    ref_type: Literal["none"] = "none"

PartyRef = Annotated[
    Union[PartyRefExisting, PartyRefNew, PartyRefNone],
    Field(discriminator="ref_type"),
]


# =====================================================================
# 4. OPERATION INPUT MODELS (1 to 22)
# =====================================================================

# --- 1. Party Lookup ---

class FindPartyOp(StrictModel):
    """1. find_party ★"""
    op: Literal["find_party"] = "find_party"
    name: RefOr[str]
    type_hint: PartyTypeHint = PartyTypeHint.ANY
    limit: Annotated[int, Field(ge=1, le=20)] = 5


# --- 2 to 8. Reads ---

class GetOutstandingOp(StrictModel):
    """2. get_outstanding ★"""
    op: Literal["get_outstanding"] = "get_outstanding"
    party: RefOr[PartyRefExisting]
    include_breakdown: bool = False

class ListAllDuesOp(StrictModel):
    """3. list_all_dues ★"""
    op: Literal["list_all_dues"] = "list_all_dues"
    direction: DuesDirection = DuesDirection.THEY_OWE_YOU
    min_amount: Optional[RefOr[Rupees]] = None
    older_than_days: Optional[Annotated[int, Field(ge=1, le=3660)]] = None
    sort: DuesSort = DuesSort.AMOUNT_DESC
    limit: Annotated[int, Field(ge=1, le=50)] = 10

class ListTransactionsOp(StrictModel):
    """4. list_transactions ★"""
    op: Literal["list_transactions"] = "list_transactions"
    party: Optional[RefOr[PartyRefExisting]] = None
    types: Optional[List[TransactionType]] = None
    status: TransactionStatusFilter = TransactionStatusFilter.CONFIRMED
    from_date: Optional[RefOr[IstDate]] = Field(default=None, alias="from")
    to_date: Optional[RefOr[IstDate]] = Field(default=None, alias="to")
    text_contains: Optional[ShortStr] = None
    min_amount: Optional[RefOr[Rupees]] = None
    max_amount: Optional[RefOr[Rupees]] = None
    order: SortOrder = SortOrder.DESC
    limit: Annotated[int, Field(ge=1, le=50)] = 10
    offset: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def validate_ranges(self) -> "ListTransactionsOp":
        if isinstance(self.from_date, date) and isinstance(self.to_date, date):
            if self.from_date > self.to_date:
                raise ValueError("'from' date cannot be after 'to' date.")
            if (self.to_date - self.from_date).days > 366:
                raise ValueError("Date range cannot exceed 366 days.")
        if isinstance(self.min_amount, Decimal) and isinstance(self.max_amount, Decimal):
            if self.min_amount > self.max_amount:
                raise ValueError("min_amount cannot exceed max_amount.")
        return self

class GetTransactionOp(StrictModel):
    """5. get_transaction"""
    op: Literal["get_transaction"] = "get_transaction"
    transaction_id: RefOr[str]

class AggregateOp(StrictModel):
    """6. aggregate ★ (replaces get_summary)"""
    op: Literal["aggregate"] = "aggregate"
    metric: AggregateMetric
    group_by: Annotated[List[GroupByDimension], Field(max_length=2)] = []
    party: Optional[RefOr[PartyRefExisting]] = None
    category: Optional[ShortStr] = None
    from_date: RefOr[IstDate] = Field(alias="from")
    to_date: RefOr[IstDate] = Field(alias="to")
    order: SortOrder = SortOrder.DESC
    limit: Annotated[int, Field(ge=1, le=50)] = 10

    @model_validator(mode="after")
    def validate_aggregate(self) -> "AggregateOp":
        if len(self.group_by) != len(set(self.group_by)):
            raise ValueError("group_by dimensions must be unique.")
        if isinstance(self.from_date, date) and isinstance(self.to_date, date):
            if self.from_date > self.to_date:
                raise ValueError("'from' date cannot be after 'to' date.")
            if (self.to_date - self.from_date).days > 366:
                raise ValueError("Date range cannot exceed 366 days.")
        return self

class GetLastEntryOp(StrictModel):
    """7. get_last_entry"""
    op: Literal["get_last_entry"] = "get_last_entry"
    scope: EntryScope = EntryScope.ANY
    party: Optional[RefOr[PartyRefExisting]] = None
    type: Optional[TransactionType] = None
    within_hours: Optional[Annotated[int, Field(ge=1, le=720)]] = None

class ListRemindersOp(StrictModel):
    """8. list_reminders"""
    op: Literal["list_reminders"] = "list_reminders"
    status: ReminderStatus = ReminderStatus.PENDING
    party: Optional[RefOr[PartyRefExisting]] = None
    limit: Annotated[int, Field(ge=1, le=50)] = 10


# --- 9 to 15. Writes (Pending Only) ---

class BaseWriteOp(StrictModel):
    """Shared parameters across write operations."""
    date: Optional[RefOr[IstDate]] = None  # Executor defaults to today IST
    note: Optional[NoteStr] = None
    category: Optional[ShortStr] = None

class _BaseTradeWriteOp(BaseWriteOp):
    """Shared validation logic for propose_sale and propose_purchase."""
    party: RefOr[PartyRef]
    total: RefOr[Rupees]
    payment_status: PaymentStatus
    paid_amount: Optional[RefOr[Rupees]] = None
    item_text: Optional[NoteStr] = None

    @model_validator(mode="after")
    def validate_trade_rules(self) -> "_BaseTradeWriteOp":
        # Rule 1: partial requires paid_amount (< total); other statuses forbid it
        if self.payment_status == PaymentStatus.PARTIAL:
            if self.paid_amount is None:
                raise ValueError("paid_amount is required when payment_status is 'partial'.")
            if isinstance(self.paid_amount, Decimal) and isinstance(self.total, Decimal):
                if self.paid_amount >= self.total:
                    raise ValueError("paid_amount must be strictly less than total for 'partial'.")
        else:
            if self.paid_amount is not None:
                raise ValueError("paid_amount is only allowed when payment_status is 'partial'.")

        # Rule 2: Unpaid or partial with PartyRefNone is invalid (debt owed by nobody)
        if isinstance(self.party, PartyRefNone):
            if self.payment_status in (PaymentStatus.UNPAID, PaymentStatus.PARTIAL):
                raise ValueError("Party cannot be 'none' when payment_status is unpaid or partial.")
        return self

class ProposeSaleOp(_BaseTradeWriteOp):
    """9. propose_sale ★"""
    op: Literal["propose_sale"] = "propose_sale"

class ProposePurchaseOp(_BaseTradeWriteOp):
    """10. propose_purchase"""
    op: Literal["propose_purchase"] = "propose_purchase"

class ProposeExpenseOp(BaseWriteOp):
    """11. propose_expense ★ (No party allowed, always fully paid)"""
    op: Literal["propose_expense"] = "propose_expense"
    amount: RefOr[Rupees]
    category: ShortStr

class ProposePaymentOp(BaseWriteOp):
    """12. propose_payment ★"""
    op: Literal["propose_payment"] = "propose_payment"
    party: RefOr[Union[PartyRefExisting, PartyRefNew]]
    amount: Optional[RefOr[Rupees]] = None
    settle_all: Optional[Literal[True]] = None
    direction: PaymentDirection = PaymentDirection.AUTO

    @model_validator(mode="after")
    def check_amount_xor_settle_all(self) -> "ProposePaymentOp":
        if (self.amount is None) == (self.settle_all is None):
            raise ValueError("Provide exactly one of 'amount' or 'settle_all=True'.")
        return self

class ProposeVoidOp(StrictModel):
    """13. propose_void"""
    op: Literal["propose_void"] = "propose_void"
    transaction_id: RefOr[str]
    reason: Optional[NoteStr] = None

class EntryChanges(StrictModel):
    """Allowed mutation fields for corrections and pending edits (type is forbidden)."""
    party: Optional[RefOr[PartyRef]] = None
    total: Optional[RefOr[Rupees]] = None
    paid_amount: Optional[RefOr[Rupees]] = None
    payment_status: Optional[PaymentStatus] = None
    date: Optional[RefOr[IstDate]] = None
    note: Optional[NoteStr] = None
    category: Optional[ShortStr] = None

    @model_validator(mode="after")
    def ensure_at_least_one_change(self) -> "EntryChanges":
        if not any(getattr(self, f) is not None for f in self.model_fields):
            raise ValueError("At least one field must be provided in 'changes'.")
        return self

class ProposeCorrectionOp(StrictModel):
    """14. propose_correction"""
    op: Literal["propose_correction"] = "propose_correction"
    transaction_id: RefOr[str]
    changes: EntryChanges

class EditPendingOp(StrictModel):
    """15. edit_pending ★"""
    op: Literal["edit_pending"] = "edit_pending"
    item_index: Optional[Annotated[int, Field(ge=0)]] = None
    changes: EntryChanges


# --- 16 to 18. Reminders ---

class CreateReminderOp(StrictModel):
    """16. create_reminder"""
    op: Literal["create_reminder"] = "create_reminder"
    party: Optional[RefOr[PartyRefExisting]] = None
    remind_at: RefOr[IstDateTime]
    note: NoteStr

class RescheduleReminderOp(StrictModel):
    """17. reschedule_reminder"""
    op: Literal["reschedule_reminder"] = "reschedule_reminder"
    reminder_id: RefOr[str]
    remind_at: RefOr[IstDateTime]

class CancelReminderOp(StrictModel):
    """18. cancel_reminder"""
    op: Literal["cancel_reminder"] = "cancel_reminder"
    reminder_id: Optional[RefOr[str]] = None
    party: Optional[RefOr[PartyRefExisting]] = None
    all_for_party: bool = False

    @model_validator(mode="after")
    def check_reminder_id_xor_party(self) -> "CancelReminderOp":
        if (self.reminder_id is None) == (self.party is None):
            raise ValueError("Provide exactly one of 'reminder_id' or 'party'.")
        if self.reminder_id is not None and self.all_for_party:
            raise ValueError("'all_for_party' can only be set when cancelling by 'party'.")
        return self


# --- 19 to 22. Utility and Control ---

class ResolveDateOp(StrictModel):
    """19. resolve_date ★"""
    op: Literal["resolve_date"] = "resolve_date"
    expression: ShortStr
    mode: DateResolveMode
    prefer: DatePrefer = DatePrefer.AUTO

class CalculateOp(StrictModel):
    """20. calculate ★"""
    op: Literal["calculate"] = "calculate"
    calc_op: CalcOp = Field(alias="op_type")
    operands: Annotated[List[Union[Rupees, Decimal, int, VarRef]], Field(min_length=2)]

    @model_validator(mode="after")
    def validate_operands(self) -> "CalculateOp":
        if self.calc_op in (CalcOp.SUBTRACT, CalcOp.DIVIDE, CalcOp.PERCENT_OF, CalcOp.PCT_CHANGE):
            if len(self.operands) != 2:
                raise ValueError(f"'{self.calc_op.value}' requires exactly 2 operands.")
        if self.calc_op == CalcOp.DIVIDE:
            denom = self.operands[1]
            if isinstance(denom, (int, Decimal)) and denom == 0:
                raise ValueError("Division by zero is invalid.")
        return self

class AskOption(StrictModel):
    id: Annotated[str, constr(min_length=1, max_length=64)]  # e.g. "party:12"
    label: Annotated[str, constr(min_length=1, max_length=72)]

class AskUserOp(StrictModel):
    """21. ask_user ★"""
    op: Literal["ask_user"] = "ask_user"
    kind: AskKind
    field: ShortStr
    options: Annotated[List[AskOption], Field(max_length=10)] = []
    slots: Dict[str, Union[VarRef, str]] = {}

class ReplyOp(StrictModel):
    """22. reply ★"""
    op: Literal["reply"] = "reply"
    template_id: ShortStr
    slots: Dict[str, Union[VarRef, str]]

    @field_validator("slots")
    @classmethod
    def forbid_numeric_literals_in_slots(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        for key, val in v.items():
            if isinstance(val, (int, float, Decimal)):
                raise ValueError(f"Slot '{key}' must be a $var reference or short text, never a raw number.")
            if isinstance(val, str) and not val.startswith("$"):
                # Catch raw numbers disguised as strings: "240", "240.50", "₹500"
                cleaned = val.replace("₹", "").replace(",", "").strip()
                try:
                    Decimal(cleaned)
                    raise ValueError(
                        f"Slot '{key}' contains numeric literal '{val}'. Numeric slots must be $var references."
                    )
                except Exception as e:
                    if "Numeric slots must be $var" in str(e):
                        raise
        return v


# =====================================================================
# 5. EXECUTION PLAN & STEP BINDING
# =====================================================================

AnyOperation = Annotated[
    Union[
        # ★ Demo set (12)
        FindPartyOp,
        GetOutstandingOp,
        ListAllDuesOp,
        ListTransactionsOp,
        AggregateOp,
        ProposeSaleOp,
        ProposeExpenseOp,
        ProposePaymentOp,
        EditPendingOp,
        ResolveDateOp,
        CalculateOp,
        AskUserOp,
        ReplyOp,
        # Full Registry (Remaining 9)
        GetTransactionOp,
        GetLastEntryOp,
        ListRemindersOp,
        ProposePurchaseOp,
        ProposeVoidOp,
        ProposeCorrectionOp,
        CreateReminderOp,
        RescheduleReminderOp,
        CancelReminderOp,
    ],
    Field(discriminator="op"),
]

class PlanStep(StrictModel):
    """Binds an operation call to a variable name (e.g., bind='ramesh' -> '$ramesh')."""
    bind: Annotated[str, Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")]
    call: AnyOperation

class ExecutionPlan(StrictModel):
    """Top-level structured output produced by the LLM."""
    steps: Annotated[List[PlanStep], Field(min_length=1, max_length=15)]


# =====================================================================
# 6. RESULT ENVELOPES (Discriminated Union on `kind`)
# =====================================================================

class PartyCandidate(StrictModel):
    id: str
    name: str
    score: float
    balance_paise: int
    balance_direction: BalanceDirection
    last_activity: Optional[IstDate] = None
    phone_last4: Optional[Annotated[str, constr(min_length=4, max_length=4)]] = None

class PartyFoundData(StrictModel):
    kind: Literal["party_found"] = "party_found"
    party: PartyCandidate

class PartyAmbiguousData(StrictModel):
    kind: Literal["party_ambiguous"] = "party_ambiguous"
    candidates: List[PartyCandidate]

class PartyNotFoundData(StrictModel):
    kind: Literal["party_not_found"] = "party_not_found"
    queried_name: str

class BalanceBreakdown(StrictModel):
    receivable_paise: int
    payable_paise: int

class BalanceData(StrictModel):
    kind: Literal["balance"] = "balance"
    party_id: str
    party_name: str
    net_balance_paise: int
    direction: BalanceDirection
    open_entry_count: int
    oldest_unpaid_date: Optional[IstDate] = None
    last_payment_date: Optional[IstDate] = None
    breakdown: Optional[BalanceBreakdown] = None

class SettledData(StrictModel):
    kind: Literal["settled"] = "settled"
    party_id: str
    party_name: str
    last_payment_date: Optional[IstDate] = None

class DueRow(StrictModel):
    party_id: str
    party_name: str
    amount_paise: int
    direction: BalanceDirection
    oldest_unpaid_date: Optional[IstDate] = None

class DuesListData(StrictModel):
    kind: Literal["dues_list"] = "dues_list"
    rows: List[DueRow]
    total_paise: int
    count: int
    truncated: bool

class NoDuesData(StrictModel):
    kind: Literal["no_dues"] = "no_dues"

class TransactionRow(StrictModel):
    transaction_id: str
    date: IstDate
    type: TransactionType
    party_id: Optional[str] = None
    party_name: Optional[str] = None
    total_paise: int
    paid_paise: int
    category: Optional[str] = None
    note: Optional[str] = None
    status: Literal["confirmed", "voided"]

class TransactionListData(StrictModel):
    kind: Literal["transaction_list"] = "transaction_list"
    rows: List[TransactionRow]
    has_more: bool

class NoResultsData(StrictModel):
    kind: Literal["no_results"] = "no_results"
    notes_were_empty: bool = False

class TransactionDetailData(StrictModel):
    kind: Literal["transaction_detail"] = "transaction_detail"
    transaction: TransactionRow

class AggregateTableData(StrictModel):
    kind: Literal["aggregate_table"] = "aggregate_table"
    table_handle: str
    metric: AggregateMetric
    preview_rows: List[Dict[str, Any]]
    is_partial_period: bool = False
    truncated: bool = False

class NoActivityData(StrictModel):
    kind: Literal["no_activity"] = "no_activity"

class EntryFoundData(StrictModel):
    kind: Literal["entry_found"] = "entry_found"
    scope: Literal["pending", "confirmed"]
    reference_id: str
    summary: Dict[str, Any]
    older_than_24h: bool = False

class NoneFoundData(StrictModel):
    kind: Literal["none_found"] = "none_found"

class ReminderRow(StrictModel):
    reminder_id: str
    party_id: Optional[str] = None
    party_name: Optional[str] = None
    remind_at: IstDateTime
    note: str
    status: ReminderStatus

class ReminderListData(StrictModel):
    kind: Literal["reminder_list"] = "reminder_list"
    rows: List[ReminderRow]

class PendingCreatedData(StrictModel):
    kind: Literal["pending_created"] = "pending_created"
    pending_action_id: str
    preview: Dict[str, Any]
    expected_balance_paise: Optional[int] = None
    resulting_balance_paise: Optional[int] = None
    resulting_direction: Optional[BalanceDirection] = None

class DateResolvedData(StrictModel):
    kind: Literal["date_resolved"] = "date_resolved"
    resolved_date: Optional[IstDate] = None
    resolved_from: Optional[IstDate] = None
    resolved_to: Optional[IstDate] = None
    resolved_datetime: Optional[IstDateTime] = None
    assumption: str

class CalcResultData(StrictModel):
    kind: Literal["calc_result"] = "calc_result"
    value_paise: Optional[int] = None
    numeric_value: Optional[Decimal] = None
    is_undefined: bool = False  # True when pct_change from 0 ("new")

class GenericStateData(StrictModel):
    kind: Literal["needs_input", "not_found", "invalid", "conflict", "error"]
    message: str
    missing_fields: List[str] = []


ResultPayload = Annotated[
    Union[
        PartyFoundData,
        PartyAmbiguousData,
        PartyNotFoundData,
        BalanceData,
        SettledData,
        DuesListData,
        NoDuesData,
        TransactionListData,
        NoResultsData,
        TransactionDetailData,
        AggregateTableData,
        NoActivityData,
        EntryFoundData,
        NoneFoundData,
        ReminderListData,
        PendingCreatedData,
        DateResolvedData,
        CalcResultData,
        GenericStateData,
    ],
    Field(discriminator="kind"),
]

class OperationResultEnvelope(StrictModel):
    """Standardized output envelope returned by the executor for every step."""
    status: ResultStatus
    kind: str
    data: ResultPayload
    warnings: List[str] = []