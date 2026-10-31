import json
from typing import Dict, Any, Type
from pydantic import BaseModel

class PromptMaker:
    def __init__(self, base_system_prompt: str, db_schema: Dict[str, Any], operations: list[Type[BaseModel]]):
        self.base_system_prompt = base_system_prompt
        # Compile static parts once during initialization
        self.compiled_schema = self._compile_schema(db_schema)
        self.compiled_operations = self._compile_operations(operations)
        
        # Cache the complete static system prompt
        self.static_system_prompt = (
            f"{self.base_system_prompt}\n\n"
            f"{self.compiled_schema}\n\n"
            f"{self.compiled_operations}"
        )

    def _compile_schema(self, schema_dict: Dict[str, Any]) -> str:
        """Parses the specific db_schema_context dictionary into a readable text format."""
        schema_text = "### Database Schema & Business Rules\n"
        
        for table_name, details in schema_dict.items():
            schema_text += f"- **{table_name}** ({details.get('type', 'table')}): {details.get('description', '')}\n"
            
            # Columns
            if "columns" in details:
                schema_text += "  - Columns:\n"
                for col_name, col_desc in details["columns"].items():
                    schema_text += f"    - {col_name}: {col_desc}\n"
            
            # Optional attributes
            for attr in ["relationships", "business_rules", "indexes"]:
                if attr in details and details[attr]:
                    schema_text += f"  - {attr.replace('_', ' ').title()}:\n"
                    for rule in details[attr]:
                        schema_text += f"    - {rule}\n"
            schema_text += "\n"
            
        return schema_text

    @staticmethod
    def _type_of(meta: Dict[str, Any], defs: Dict[str, Any]) -> str:
        """Readable type from a JSON-schema fragment (handles anyOf / $ref / enums)."""
        if "$ref" in meta:
            name = meta["$ref"].split("/")[-1]
            target = defs.get(name, {})
            if "enum" in target:
                return "|".join(map(str, target["enum"]))
            if "properties" in target:
                inner = ", ".join(
                    f"{k}: {PromptMaker._type_of(v, defs)}" for k, v in target["properties"].items()
                )
                return "{" + inner + "}"
            return name
        if "enum" in meta:
            return "|".join(map(str, meta["enum"]))
        if "const" in meta:
            return json.dumps(meta["const"])
        if "anyOf" in meta or "oneOf" in meta:
            parts = []
            for m in meta.get("anyOf", meta.get("oneOf", [])):
                if m.get("pattern", "").startswith("^\\$"):
                    parts.append("\"$var\"")
                elif m.get("type") == "null":
                    continue
                else:
                    parts.append(PromptMaker._type_of(m, defs))
            return " | ".join(dict.fromkeys(parts))
        t = meta.get("type", "any")
        if t == "array":
            return "[" + PromptMaker._type_of(meta.get("items", {}), defs) + "]"
        if t == "object":
            return "object"
        return t

    def _compile_operations(self, operations: list[Type[BaseModel]]) -> str:
        """One line per operation using the EXACT `op` string and JSON key names the executor expects."""
        ops_text = (
            "### Available Operations (use the exact `op` string and key names shown)\n"
            "Money fields are rupee decimals as strings like \"50.00\". A `$var` string may replace any value.\n"
        )
        for op in operations:
            schema = op.model_json_schema(by_alias=True)
            defs = schema.get("$defs", {})
            props = schema.get("properties", {})
            required = schema.get("required", [])
            op_name = props.get("op", {}).get("default") or op.__name__
            fields = []
            for field, meta in props.items():
                if field == "op":
                    continue
                opt = "" if field in required else "?"
                fields.append(f"{field}{opt}: {self._type_of(meta, defs)}")
            ops_text += f"- `{op_name}`: " + ", ".join(fields) + "\n"
        return ops_text

    def build_prompt(self, user_text: str, current_ist_time: str, current_business_id: str, extra_context: str = "") -> str:
        """
        Appends dynamic request variables to the pre-compiled static system prompt.
        Call this for every new LLM generation.
        """
        dynamic_context = (
            "### Current Request Context\n"
            f"- **Business ID**: {current_business_id}\n"
            f"- **Current IST Time**: {current_ist_time}\n"
        )
        
        if extra_context:
            dynamic_context += f"- **State Context**: {extra_context}\n"
            
        dynamic_context += f"\n**User Input**: \"{user_text}\"\n"
        
        return f"{self.static_system_prompt}\n{dynamic_context}\n**Generate Execution Plan:**\n"
