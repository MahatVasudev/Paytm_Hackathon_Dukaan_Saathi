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
    return (Decimal(paise) / Decimal("100")).quantize(Decimal("0.01"))

# 2. Execution Context
class ExecutionContext:
    def __init__(self, conn: AsyncConnection, business_id: str, now_ist: datetime):
        self.conn = conn
        self.business_id = business_id
        self.now_ist = now_ist
        self.state: Dict[str, Any] = {}  # Stores results of step bindings ($vars)

    def resolve_ref(self, value: Any) -> Any:
        """Resolves a $var reference, including dot-notation like $party_1.id or $party_1.party_id."""
        if not (isinstance(value, str) and value.startswith("$")):
            return value

        ref_path = value[1:].split(".")
        root_var = ref_path[0]

        if root_var not in self.state:
            raise ValueError(f"Unresolved reference: {value} (root variable '{root_var}' not found in state)")

        current = self.state[root_var]

        # Traverse nested keys if dot-notation is used (e.g. $party_1.id)
        for segment in ref_path[1:]:
            if isinstance(current, dict):
                # Normalize 'id' vs 'party_id' lookups
                if segment in current:
                    current = current[segment]
                elif segment == "id" and "party_id" in current:
                    current = current["party_id"]
                elif segment == "party_id" and "id" in current:
                    current = current["id"]
                elif "candidates" in current and len(current["candidates"]) == 1:
                    # Single unambiguous find_party match: allow any of its fields ($p.name, $p.balance, ...)
                    cand = current["candidates"][0]
                    key = "party_id" if segment == "id" else "name" if segment == "party_name" else segment
                    if key not in cand:
                        raise ValueError(f"Key '{segment}' not found when resolving path '{value}' in {cand}")
                    current = cand[key]
                elif segment == "party_name" and "name" in current:
                    current = current["name"]
                elif segment == "name" and "party_name" in current:
                    current = current["party_name"]
                else:
                    raise ValueError(f"Key '{segment}' not found when resolving path '{value}' in {current}")
            elif hasattr(current, segment):
                current = getattr(current, segment)
            else:
                raise ValueError(f"Cannot traverse segment '{segment}' on type {type(current)} when resolving '{value}'")

        # If the LLM passed just '$var' directly where a scalar was expected
        if isinstance(current, dict):
            for key in ("resolved_date", "result"):
                if key in current:
                    return current[key]
            if "party_id" in current:
                return current["party_id"]
            if "id" in current:
                return current["id"]
            if "candidates" in current and len(current["candidates"]) == 1:
                return current["candidates"][0].get("party_id")

        return current

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
