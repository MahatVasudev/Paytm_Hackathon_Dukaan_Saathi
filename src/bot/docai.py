"""Document extraction with Sarvam Document Intelligence.
Uses a highly generic schema to act as OCR for ANY document type (invoices, handwritten notes, payment screenshots)."""
import json
import os
import time

import requests

BASE = "https://api.sarvam.ai/doc-ai/v1"
TERMINAL = {"completed", "partially_completed", "failed", "rejected"}

# Generic schema: Just grab all readable text line-by-line
SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "extracted_lines": {
            "type": "array",
            "description": "Extract all readable text, items, amounts, names, or details from the document line by line exactly as they appear.",
            "items": {
                "type": "string"
            }
        }
    }
})

class DocAIError(Exception):
    """Raised with a user-friendly message."""

def extract(file_bytes: bytes, filename: str, content_type: str, timeout_s: int = 180) -> dict:
    """Blocking call (it polls), so the bot runs it in a worker thread."""
    headers = {"api-subscription-key": os.environ["SARVAM_API_KEY"]}

    submit = requests.post(
        f"{BASE}/job/extract", headers=headers,
        files={"file": (filename, file_bytes, content_type)},
        data={
            "schema": SCHEMA,
            "language": os.environ.get("DOCAI_LANGUAGE", "hi-IN"),
            "output_format": "json",
            "classification": "false",
            "auto_orient": "true",
        },
        timeout=120,
    )
    submit.raise_for_status()
    job_id = submit.json()["job_id"]

    deadline = time.monotonic() + timeout_s
    while True:
        r = requests.get(f"{BASE}/job/{job_id}/status", headers=headers, timeout=30)
        r.raise_for_status()
        status = r.json()
        if status["status"] in TERMINAL:
            break
        if time.monotonic() > deadline:
            raise DocAIError("Reading the document took too long. Please try again.")
        time.sleep(2)

    if status["status"] in {"failed", "rejected"}:
        raise DocAIError("I couldn't read that document. Please send a clearer PDF or photo.")

    r = requests.get(f"{BASE}/job/{job_id}/results", headers=headers,
                     params={"format": "json"}, timeout=60)
    r.raise_for_status()
    body = r.json()
    return {"result": body.get("result") or {}}


def format_result(data: dict) -> str:
    """Formats the raw extracted lines into a clean text list."""
    result = data.get("result", {})
    lines = result.get("extracted_lines", [])
    
    if not lines:
        return "Could not extract readable text from the document. Please try a clearer image."
        
    # Join the lines neatly so the user can review them and the LLM can parse them
    return "\n".join(f"• {line}" for line in lines)