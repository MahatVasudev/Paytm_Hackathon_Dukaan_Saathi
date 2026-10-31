from sqlalchemy import (
    MetaData,
    Table,
    Column,
    String,
    BigInteger,
    Integer,
    DateTime,
    Date,
    ForeignKey
)
from sqlalchemy.dialects.postgresql import JSONB

# Shared metadata object for the Core tables
metadata = MetaData()

# ---------------------------------------------------------
# 1. Core Business & Identity
# ---------------------------------------------------------
businesses = Table(
    "businesses", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("name", String, nullable=False),
    Column("channel", String, nullable=False, server_default="telegram"),
    Column("external_chat_id", String, nullable=False),
    Column("language_pref", String, nullable=False, server_default="en"),
    Column("created_at", DateTime(timezone=True), nullable=False)
)

parties = Table(
    "parties", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("business_id", BigInteger, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
    Column("name", String, nullable=False),
    Column("normalized_name", String, nullable=False),
    Column("phone", String),
    Column("type", String, nullable=False, server_default="customer"),
    Column("created_at", DateTime(timezone=True), nullable=False)
)

party_aliases = Table(
    "party_aliases", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("party_id", BigInteger, ForeignKey("parties.id", ondelete="CASCADE"), nullable=False),
    Column("alias", String, nullable=False),
    Column("normalized_alias", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False)
)

# ---------------------------------------------------------
# 2. Communications & Media
# ---------------------------------------------------------
messages = Table(
    "messages", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("business_id", BigInteger, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
    Column("direction", String, nullable=False),
    Column("type", String, nullable=False),
    Column("text", String),
    Column("transcript", String),
    Column("transcription_status", String, nullable=False, server_default="not_needed"),
    Column("language_detected", String),
    Column("external_message_id", String),
    Column("update_id", BigInteger),
    Column("party_id", BigInteger), # Note: Postgres handles the composite FK constraint internally
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("processed_at", DateTime(timezone=True))
)

media = Table(
    "media", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("message_id", BigInteger, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String, nullable=False),
    Column("mime_type", String),
    Column("size_bytes", BigInteger),
    Column("external_file_id", String),
    Column("storage_path", String),
    Column("sha256", String),
    Column("downloaded_at", DateTime(timezone=True)),
    Column("deleted_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False)
)

# ---------------------------------------------------------
# 3. Core Ledger & Execution
# ---------------------------------------------------------
transactions = Table(
    "transactions", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("business_id", BigInteger, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
    Column("party_id", BigInteger),
    Column("type", String, nullable=False),
    Column("total_amount", BigInteger, nullable=False),
    Column("paid_amount", BigInteger, nullable=False, server_default="0"),
    Column("category", String),
    Column("note", String),
    Column("transaction_date", Date, nullable=False),
    Column("status", String, nullable=False, server_default="pending"),
    Column("source", String, nullable=False, server_default="text"),
    Column("source_message_id", BigInteger, ForeignKey("messages.id")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("confirmed_at", DateTime(timezone=True)),
    Column("voided_at", DateTime(timezone=True))
)

pending_actions = Table(
    "pending_actions", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("business_id", BigInteger, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
    Column("intent", String, nullable=False),
    Column("collected_fields", JSONB, nullable=False, server_default="'{}'::jsonb"),
    Column("awaiting", String, nullable=False),
    Column("status", String, nullable=False, server_default="open"),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False)
)

agent_runs = Table(
    "agent_runs", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("message_id", BigInteger, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
    Column("step_no", Integer, nullable=False),
    Column("operation", String, nullable=False),
    Column("params", JSONB),
    Column("result_summary", JSONB),
    Column("error", String),
    Column("duration_ms", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False)
)

reminders = Table(
    "reminders", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("business_id", BigInteger, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False),
    Column("party_id", BigInteger),
    Column("note", String),
    Column("remind_at", DateTime(timezone=True), nullable=False),
    Column("status", String, nullable=False, server_default="pending"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("last_error", String),
    Column("sent_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False)
)

# ---------------------------------------------------------
# 4. Views
# ---------------------------------------------------------
# SQLAlchemy Core treats views just like tables for SELECT queries.
party_balances = Table(
    "party_balances", metadata,
    Column("business_id", BigInteger, primary_key=True),
    Column("party_id", BigInteger, primary_key=True),
    Column("balance", BigInteger)
)
