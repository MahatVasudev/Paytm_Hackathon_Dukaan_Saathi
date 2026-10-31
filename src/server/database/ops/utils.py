import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict
from sqlalchemy import Table, Column, Integer, String, MetaData, select, insert, and_
from sqlalchemy.ext.asyncio import AsyncConnection

# 1. Money Boundaries (Strict Conversion)
def to_paise(rupees: Decimal) -> int:
    """Converts Rupees (Decimal) to Paise (Integer) for DB writes."""
    return int((rupees * Decimal("100")).to_integral_value())

def to_rupees(paise: int) -> Decimal:
    """Converts Paise (Integer) to Rupees (Decimal) for DB reads/LLM outputs."""
    return Decimal(paise) / Decimal("100")

# 2. Execution Context
class ExecutionContext:
    def __init__(self, conn: AsyncConnection, business_id: str, now_ist: datetime):
        self.conn = conn
        self.business_id = business_id
        self.now_ist = now_ist
        self.state: Dict[str, Any] = {}  # Stores results of step bindings ($vars)

    def resolve_ref(self, value: Any) -> Any:
        """Resolves a $var reference from the current state."""
        if isinstance(value, str) and value.startswith("$"):
            var_name = value[1:]
            if var_name not in self.state:
                raise ValueError(f"Unresolved reference: {value}")
            return self.state[var_name]
        return value

    def resolve_model(self, model_dump: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively resolves all $vars in a Pydantic model dump."""
        resolved = {}
        for k, v in model_dump.items():
            if isinstance(v, dict):
                resolved[k] = self.resolve_model(v)
            elif isinstance(v, list):
                resolved[k] = [
                    self.resolve_model(i) if isinstance(i, dict) else self.resolve_ref(i) 
                    for i in v
                ]
            else:
                resolved[k] = self.resolve_ref(v)
        return resolved

# Envelope for all returns
class ResultEnvelope:
    def __init__(self, status: str, data: Any = None, error: str | None = None):
        self.status = status
        self.data = data
        self.error = error
