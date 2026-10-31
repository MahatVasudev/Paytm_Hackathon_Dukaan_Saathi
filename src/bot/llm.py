import os
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
import aiohttp
from dotenv import load_dotenv
from groq import AsyncGroq, BadRequestError

# Pydantic models & registry
from src.server.types.pydantic import (
    ExecutionPlan, PlanStep,
    FindPartyOp, GetOutstandingOp, ListAllDuesOp, ListTransactionsOp,
    GetTransactionOp, AggregateOp, GetLastEntryOp, ListRemindersOp,
    ProposeSaleOp, ProposePurchaseOp, ProposeExpenseOp, ProposePaymentOp,
    ProposeVoidOp, ProposeCorrectionOp, EditPendingOp, CreateReminderOp,
    RescheduleReminderOp, CancelReminderOp, ResolveDateOp, CalculateOp,
    AskUserOp, ReplyOp
)

# Database handlers & context
from src.server.database.ops.utils import ExecutionContext, ResultEnvelope
from src.server.database.ops import (
    execute_find_party, execute_get_outstanding, execute_list_all_dues,
    execute_list_transactions, execute_get_transaction, execute_aggregate,
    execute_get_last_entry, execute_list_reminders, execute_propose_sale,
    execute_propose_purchase, execute_propose_expense, execute_propose_payment,
    execute_propose_void, execute_propose_correction, execute_create_reminder,
    execute_reschedule_reminder, execute_cancel_reminder, execute_resolve_date,
    execute_calculate, execute_ask_user, execute_reply, execute_edit_pending,
    execute_confirm_pending
)

# Dynamic PromptMaker & Base Prompt
from src.server.utils import PromptMaker
from src.server.description.base_prompt import BASE_PROMPT_V1 

import re
from pydantic import ValidationError
from src.server.utils.translit import search_key

load_dotenv()
logger = logging.getLogger(__name__)

API_URL = "https://api.sarvam.ai/v1/chat/completions"
IST = timezone(timedelta(hours=5, minutes=30))

# All 22 operations for the schema prompt
ALL_OPERATIONS = [
    FindPartyOp, GetOutstandingOp, ListAllDuesOp, ListTransactionsOp,
    GetTransactionOp, AggregateOp, GetLastEntryOp, ListRemindersOp,
    ProposeSaleOp, ProposePurchaseOp, ProposeExpenseOp, ProposePaymentOp,
    ProposeVoidOp, ProposeCorrectionOp, EditPendingOp, CreateReminderOp,
    RescheduleReminderOp, CancelReminderOp, ResolveDateOp, CalculateOp,
    AskUserOp, ReplyOp
]

# Dispatcher mapping
OPERATION_HANDLERS = {
    "find_party": execute_find_party,
    "get_outstanding": execute_get_outstanding,
    "list_all_dues": execute_list_all_dues,
    "list_transactions": execute_list_transactions,
    "get_transaction": execute_get_transaction,
    "aggregate": execute_aggregate,
    "get_last_entry": execute_get_last_entry,
    "list_reminders": execute_list_reminders,
    "propose_sale": execute_propose_sale,
    "propose_purchase": execute_propose_purchase,
    "propose_expense": execute_propose_expense,
    "propose_payment": execute_propose_payment,
    "propose_void": execute_propose_void,
    "propose_correction": execute_propose_correction,
    "edit_pending": execute_edit_pending,
    "create_reminder": execute_create_reminder,
    "reschedule_reminder": execute_reschedule_reminder,
    "cancel_reminder": execute_cancel_reminder,
    "resolve_date": execute_resolve_date,
    "calculate": execute_calculate,
    "ask_user": execute_ask_user,
    "reply": execute_reply,
}


YES_WORDS = {"yes", "y", "confirm", "confirmed", "ok", "okay", "save", "haan", "han", "ha", "haa", "theek hai",
             "thik hai", "sahi hai", "done", "correct"}
NO_WORDS = {"no", "n", "cancel", "nahi", "nahin", "na", "stop", "discard"}
WRITE_OPS = {"propose_sale", "propose_purchase", "propose_payment", "create_reminder"}


def extract_plan_json(text: str) -> Optional[str]:
    """Find the JSON object containing a non-empty "steps" list inside free text
    (e.g. a model that reasoned out loud before answering). Prefers the last such object."""
    decoder = json.JSONDecoder()
    best = None
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[i:])
        except ValueError:
            continue
        steps = obj.get("steps") if isinstance(obj, dict) else None
        if isinstance(steps, list) and steps:
            best = obj          # keep scanning: the final answer comes last
    return json.dumps(best) if best else None


class Session:
    """Execution state for ONE business/chat; survives across messages while we wait for an answer."""
    def __init__(self):
        self.reset()

    def reset(self, keep_memory: bool = False):
        if not keep_memory:
            # Decisions made while resolving ONE user request; they must survive replans, otherwise
            # the same question gets asked again after every answer.
            self.memory: Dict[str, Any] = {"new_parties": {}, "chosen": {}, "history": []}
        self.plan: List[Dict[str, Any]] = []
        self.step_index: int = 0
        self.state: Dict[str, Any] = {}          # $var bindings from executed steps
        self.original_text: str = ""
        self.awaiting: Optional[Dict[str, Any]] = None


class DukaanOrchestrator:
    def __init__(self, db_schema_context: Dict[str, Any]):
        # Cache static prompt (schema + operations) once
        self.prompt_maker = PromptMaker(
            base_system_prompt=BASE_PROMPT_V1,
            db_schema=db_schema_context,
            operations=ALL_OPERATIONS
        )
        # Per-business conversation state (the orchestrator is shared by all chats)
        self.sessions: Dict[Any, Session] = {}

        groq_api_key = os.environ.get("GROQ_API_KEY")
        self.groq_client = AsyncGroq(api_key=groq_api_key) if groq_api_key else None
    # Replace _call_sarvam in bot/llm.py with this:

    async def _call_groq(
            self, 
            prompt: str, 
            user_text: str, 
            model: str = "llama-3.3-70b-versatile"
        ) -> str:
            """
            Executes fast, structured JSON inference via Groq.
            Recommended models:
              - 'llama-3.3-70b-versatile' (High precision for complex multi-step plans)
              - 'llama-3.1-8b-instant' (Ultra-low latency for simple turns)
            """
            if not self.groq_client:
                raise ValueError("GROQ_API_KEY environment variable is missing.")

            try:
                kwargs = {}
                if "gpt-oss" in model:
                    kwargs["reasoning_effort"] = "low"   # less chain-of-thought leaking into the answer
                response = await self.groq_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_text}
                    ],
                    temperature=0.0,
                    max_tokens=4096,
                    # Enforces valid JSON emission directly at the engine level
                    response_format={"type": "json_object"},
                    **kwargs,
                )

                raw_text = response.choices[0].message.content or ""
                raw_text = raw_text.strip()

                if not raw_text:
                    raise ValueError("Groq returned an empty response.")

                # Safety fallback: clean any remaining markdown fence tags
                raw_text = raw_text.replace("```json", "").replace("```", "").strip()

                # Isolate JSON object if wrapped
                match = re.search(r"(\{[\s\S]*\})", raw_text)
                return match.group(1).strip() if match else raw_text

            except BadRequestError as e:
                # JSON mode rejects outputs where the model "thought out loud"; the plan is usually
                # still inside failed_generation, so recover it instead of failing the request.
                body = getattr(e, "body", None) or {}
                err = body.get("error", body) if isinstance(body, dict) else {}
                recovered = extract_plan_json(str(err.get("failed_generation", "")))
                if recovered:
                    logger.warning("Recovered plan JSON from Groq failed_generation")
                    return recovered
                logger.exception("Groq inference error")
                raise
            except Exception:
                logger.exception("Groq inference error")
                raise

    async def _call_sarvam(self, prompt: str, user_text: str) -> str:
        """Calls Sarvam AI synchronously (stream=False) to ensure complete responses."""
        api_key = os.environ.get("SARVAM_API_KEY")
        if not api_key:
            raise ValueError("SARVAM_API_KEY environment variable is missing.")

        headers = {
            "api-subscription-key": api_key,
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": "sarvam-105b",
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_text}
            ],
            "temperature": 0.1,
            "top_p": 1,
            "max_tokens": 1024,
            "stream": False  # Disable streaming
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, headers=headers, json=payload) as resp:
                raw_body = await resp.text()
                
                if resp.status != 200:
                    logger.error(f"Sarvam HTTP error {resp.status}: {raw_body}")
                    raise RuntimeError(f"Sarvam API error ({resp.status}): {raw_body}")

                try:
                    data = json.loads(raw_body)
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode response as JSON. Raw body: {raw_body}")
                    raise ValueError("Invalid JSON response from Sarvam.")

                # Log entire payload to inspect structure
                logger.info(f"Sarvam API Full Response:\n{json.dumps(data, indent=2)}")

                # Extract message content
                choices = data.get("choices", [])
                if not choices:
                    raise ValueError(f"No choices returned by Sarvam. Full response: {data}")

                message = choices[0].get("message", {})
                raw_text = (message.get("content") or "").strip()
                reasoning_text = (message.get("reasoning_content") or "").strip()

                if not raw_text:
                    finish_reason = choices[0].get("finish_reason")
                    # If content is empty but model put output inside reasoning
                    if reasoning_text:
                        match = re.search(r"(\{[\s\S]*\})", reasoning_text)
                        if match:
                            return match.group(1).strip()
                    raise ValueError(
                        f"Sarvam returned empty content. finish_reason: '{finish_reason}'. "
                        f"Reasoning length: {len(reasoning_text)} chars."
                    )

                match = re.search(r"(\{[\s\S]*\})", raw_text)
                if match:
                    return match.group(1).strip()

                return raw_text
    def _extract_steps(self, obj: Any) -> Optional[List[Dict[str, Any]]]:
        """Recursively finds a non-empty list associated with key 'steps'."""
        if isinstance(obj, dict):
            if isinstance(obj.get("steps"), list) and obj["steps"]:
                return obj["steps"]
            for v in obj.values():
                found = self._extract_steps(v)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = self._extract_steps(item)
                if found:
                    return found
        return None

    @staticmethod
    def _sanitize_steps(steps: List[Any]) -> None:
        """Fix common LLM slips in place before Pydantic validation."""
        kind_map = {
            "confirm": "confirm_assumption", "confirmation": "confirm_assumption",
            "choose": "choose_option", "option": "choose_option",
            "party": "choose_party", "missing": "missing_field",
        }
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue

            # Repair mis-nested steps: {"bind":..,"call":"list_transactions","party":{..}} or
            # {"bind":..,"op":"..","party":{..}} -> params belong INSIDE "call".
            extras = {k: v for k, v in step.items() if k not in ("bind", "call")}
            if isinstance(step.get("call"), str):
                step["call"] = {"op": step["call"]}
            elif not isinstance(step.get("call"), dict) and "op" in extras:
                step["call"] = {}
            if isinstance(step.get("call"), dict) and extras:
                for k, v in extras.items():
                    step["call"].setdefault(k, v)
                    del step[k]

            if not isinstance(step.get("bind"), str) or not step["bind"].lstrip("$"):
                step["bind"] = f"step_{idx + 1}"
            else:
                step["bind"] = step["bind"].lstrip("$")

            call = step.get("call")
            if not isinstance(call, dict):
                continue
            op = call.get("op")

            if op in ("propose_sale", "propose_purchase"):
                if call.get("payment_status") in ("unpaid", "paid_in_full"):
                    call.pop("paid_amount", None)
                elif call.get("payment_status") == "partial" and not call.get("paid_amount"):
                    call["payment_status"] = "unknown"   # plan validation requires an amount; executor will ask
            elif op in ("aggregate", "list_transactions"):
                # LLMs often use the python field names instead of the aliases
                for py, alias in (("from_date", "from"), ("to_date", "to")):
                    if py in call and alias not in call:
                        call[alias] = call.pop(py)
            elif op == "ask_user":
                if call.get("kind") in kind_map:
                    call["kind"] = kind_map[call["kind"]]
                opts = call.get("options") or []
                if not opts and call.get("kind") == "confirm_assumption":
                    opts = [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]
                normalized = []
                for opt in opts:
                    if isinstance(opt, str):
                        normalized.append({"id": opt.lower().strip(), "label": opt.strip().capitalize()})
                    elif isinstance(opt, dict):
                        if "id" in opt and "label" not in opt:
                            opt["label"] = str(opt["id"]).capitalize()
                        normalized.append(opt)
                call["options"] = normalized

    async def _generate_execution_plan(
        self, user_text: str, ctx: ExecutionContext, extra_context: str = ""
    ) -> ExecutionPlan:
        now_str = ctx.now_ist.strftime("%Y-%m-%d %H:%M:%S IST (%A)")
        last_error = ""
        for attempt in range(2):
            context = extra_context
            if last_error:
                context += (f" PREVIOUS ATTEMPT WAS REJECTED: {last_error} "
                            "Fix this and return the corrected full plan.")
            full_prompt = self.prompt_maker.build_prompt(
                user_text=user_text,
                current_ist_time=now_str,
                current_business_id=str(ctx.business_id),
                extra_context=context.strip(),
            )
            json_str = await self._call_groq(full_prompt, user_text, model="openai/gpt-oss-120b")
            try:
                data = json.loads(json_str)
                steps = self._extract_steps(data)
                if not steps:
                    raise ValueError("LLM response did not contain a non-empty 'steps' list.")
                self._sanitize_steps(steps)
                return ExecutionPlan(steps=steps)
            except Exception as e:
                logger.error("Plan validation failed (attempt %d). Payload:\n%s", attempt + 1, json_str)
                last_error = str(e)[:600]
        raise ValueError(f"Plan validation failed: {last_error}")

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    async def handle_message(self, user_text: str, ctx: ExecutionContext,
                             is_clarification_reply: bool = False) -> Dict[str, Any]:
        """Main entry point for WhatsApp/Telegram inbound messages."""
        sess = self.sessions.setdefault(ctx.business_id, Session())
        ctx.state = sess.state  # shared reference: bindings persist across turns

        # Deterministic confirm/cancel of a staged entry (never routed through the LLM)
        word = user_text.strip().lower().rstrip("!. ")
        if not sess.awaiting and (word in YES_WORDS or word in NO_WORDS):
            res = await self.confirm_pending(ctx, cancel=word in NO_WORDS)
            if res["type"] != "error" or res.get("had_pending"):
                return res

        try:
            if is_clarification_reply and sess.awaiting:
                if self._try_direct_resolution(sess, user_text):
                    sess.awaiting = None
                    return await self._run_plan_loop(sess, ctx)

                # Flowchart: Direct Answer? No -> update planning & action using the answer
                question = sess.awaiting
                original = sess.original_text
                answer = user_text.split(":", 1)[1] if user_text.startswith("opt:") else user_text
                memory = sess.memory
                memory["history"].append({"asked": question.get("issue"), "answer": answer})
                sess.reset(keep_memory=True); ctx.state = sess.state
                sess.original_text = original
                qa = "; ".join(f"asked {json.dumps(h['asked'], default=str)} -> user answered \"{h['answer']}\""
                               for h in memory["history"])
                new_p = ", ".join(memory["new_parties"].values())
                clar = (f"The user's original request was: \"{original}\". Clarifications so far: {qa}. "
                        + (f"Parties the user chose to ADD AS NEW (still call find_party for them as usual): {new_p}. " if new_p else "")
                        + "Produce the full corrected plan using ALL of these answers. Do not ask again for anything already answered.")
                plan = await self._generate_execution_plan(original or answer, ctx, extra_context=clar)
            else:
                sess.reset(); ctx.state = sess.state
                sess.original_text = user_text
                plan = await self._generate_execution_plan(user_text, ctx)

            sess.plan = plan.model_dump(mode="json", by_alias=True, exclude_none=True)["steps"]
            sess.step_index = 0
            return await self._run_plan_loop(sess, ctx)
        except Exception as e:
            logger.exception("handle_message failed")
            sess.reset()
            return {"type": "error", "message": f"Sorry, I couldn't process that: {e}"}

    async def confirm_pending(self, ctx: ExecutionContext, action_id: int | None = None,
                              cancel: bool = False) -> Dict[str, Any]:
        """Commit (or cancel) the staged pending action. Used by text 'yes' and the confirm buttons."""
        try:
            async with ctx.conn.begin_nested():
                env = await execute_confirm_pending(ctx, action_id, cancel)
        except Exception as e:
            logger.exception("confirm_pending failed")
            return {"type": "error", "message": f"Could not save: {e}", "had_pending": True}
        if env.status == "ok":
            return {"type": "cancelled" if cancel else "committed", "data": env.data}
        if env.status == "not_found":
            return {"type": "error", "message": env.data.get("message", "Nothing to confirm."), "had_pending": False}
        return {"type": "error", "message": env.error or "Operation failed.", "had_pending": True}

    # ------------------------------------------------------------------
    # Clarification handling (Flowchart: Direct Answer -> directly change the query)
    # ------------------------------------------------------------------
    @staticmethod
    def _is_ref_to(value: Any, bind: str) -> bool:
        return isinstance(value, str) and value in (f"${bind}", f"${bind}.id", f"${bind}.party_id")

    def _apply_new_party(self, sess: Session, bind: str, name: str) -> None:
        """Bind `bind` to a brand-new party and point later steps that used it at the new party."""
        sess.state[bind] = {"ref_type": "new", "name": name}
        for step in sess.plan[sess.step_index + 1:]:
            call = step.get("call", {})
            party = call.get("party")
            if self._is_ref_to(party, bind) or (
                isinstance(party, dict) and self._is_ref_to(party.get("party_id"), bind)
            ):
                call["party"] = {"ref_type": "new", "name": name}

    def _try_direct_resolution(self, sess: Session, user_text: str) -> bool:
        aw = sess.awaiting
        if not aw:
            return False
        op, bind = aw.get("op"), aw["bind"]
        raw = user_text[4:] if user_text.startswith("opt:") else user_text
        cleaned = raw.strip().lower()

        # ---- payment status / paid amount for a sale or purchase ---------------------------
        if op in ("propose_sale", "propose_purchase"):
            call = sess.plan[sess.step_index]["call"]
            field = (aw.get("issue") or {}).get("field")
            if field == "payment_status":
                mapping = [(("part", "partial", "some"), "partial"),
                           (("udhaar", "udhar", "unpaid", "credit", "baaki", "baki", "not paid", "nahi"), "unpaid"),
                           (("paid", "full", "cash", "diya", "de diya", "yes"), "paid_in_full")]
                for words, status in mapping:
                    if any(w in cleaned for w in words):
                        call["payment_status"] = status
                        call.pop("paid_amount", None) if status != "partial" else None
                        return True        # same step re-runs (asks for the amount if partial)
                return False
            if field == "paid_amount":
                m = re.search(r"\d+(?:[.,]\d+)?", raw.translate(str.maketrans("०१२३४५६७८९", "0123456789")))
                if not m:
                    return False
                paid = m.group(0).replace(",", "")
                total = call.get("total")
                try:
                    if isinstance(total, str) and not total.startswith("$") and float(paid) >= float(total):
                        call["payment_status"] = "paid_in_full"   # paid everything: not a partial payment
                        call.pop("paid_amount", None)
                        return True
                except ValueError:
                    pass
                call["paid_amount"] = paid
                return True
            return False

        if op != "find_party":
            return False

        # ---- party questions ------------------------------------------------------------
        qname = (aw.get("issue") or {}).get("queried_name") or ""
        qkey = search_key(qname) if qname else ""
        if user_text.startswith("new_party:"):
            name = user_text.split(":", 1)[1].strip()
            sess.memory["new_parties"][search_key(name)] = name
            self._apply_new_party(sess, bind, name)
            sess.step_index += 1
            return True

        selected = None
        if user_text.startswith("party:"):
            selected = user_text.split(":", 1)[1]
        else:
            candidates = (aw.get("issue") or {}).get("candidates", [])
            if cleaned.isdigit() and 0 < int(cleaned) <= len(candidates):
                selected = candidates[int(cleaned) - 1]["party_id"]
            else:
                for c in candidates:
                    if str(c.get("party_id")) == cleaned or c.get("name", "").lower() == cleaned:
                        selected = c["party_id"]
                        break
        if selected is None:
            return False
        if qkey:
            sess.memory["chosen"][qkey] = str(selected)
        sess.state[bind] = {"party_id": str(selected), "id": str(selected)}
        sess.step_index += 1
        return True

    # ------------------------------------------------------------------
    # Planning & Action loop
    # ------------------------------------------------------------------
    @staticmethod
    def _coerce_party_refs(call: Dict[str, Any]) -> None:
        """After $var resolution a party may be a bare id; wrap it into a PartyRef dict."""
        def fix(container: Dict[str, Any]):
            v = container.get("party")
            if v is None:
                return
            if isinstance(v, dict):
                if v.get("ref_type", "existing") == "existing" and "party_id" in v:
                    v.setdefault("ref_type", "existing")
                    v["party_id"] = str(v["party_id"])
            else:
                container["party"] = {"ref_type": "existing", "party_id": str(v)}
        fix(call)
        if isinstance(call.get("changes"), dict):
            fix(call["changes"])

    async def _run_plan_loop(self, sess: Session, ctx: ExecutionContext) -> Dict[str, Any]:
        """Run steps sequentially; pause on anything that needs the user (Flowchart: Facing Any Issues?)."""
        while sess.step_index < len(sess.plan):
            step = sess.plan[sess.step_index]
            bind_var, call_dict = step["bind"], step["call"]
            op_name = call_dict["op"]

            handler = OPERATION_HANDLERS.get(op_name)
            if not handler:
                sess.reset()
                return {"type": "error", "message": f"Unsupported operation '{op_name}'."}

            # A party question already answered earlier in this conversation: reuse the answer
            if op_name == "find_party" and isinstance(call_dict.get("name"), str) and not call_dict["name"].startswith("$"):
                qkey = search_key(call_dict["name"])
                if qkey in sess.memory["chosen"]:
                    pid = sess.memory["chosen"][qkey]
                    sess.state[bind_var] = {"party_id": pid, "id": pid}
                    sess.step_index += 1
                    continue
                if qkey in sess.memory["new_parties"]:
                    self._apply_new_party(sess, bind_var, sess.memory["new_parties"][qkey])
                    sess.step_index += 1
                    continue

            # Partial payment needs an amount: ask directly instead of failing validation
            if op_name in ("propose_sale", "propose_purchase") and call_dict.get("payment_status") == "partial" \
                    and not call_dict.get("paid_amount"):
                data = {"field": "paid_amount", "message": "How much was paid now? (e.g. 50)"}
                sess.awaiting = {"step_index": sess.step_index, "bind": bind_var, "op": op_name,
                                 "status": "needs_input", "issue": data}
                return {"type": "clarification_needed", "issue": "needs_input", "data": data}

            try:
                if op_name == "reply":
                    # Slots are cosmetic (output comes from templates + real data): never fail on a bad $ref
                    resolved = {**call_dict, "slots": {}}
                    for k, v in (call_dict.get("slots") or {}).items():
                        try:
                            resolved["slots"][k] = ctx.resolve_ref(v)
                        except Exception:
                            logger.warning("Unresolvable reply slot %s=%r ignored", k, v)
                            resolved["slots"][k] = ""
                else:
                    resolved = ctx.resolve_model(call_dict)
                self._coerce_party_refs(resolved)
                op_cls = next(op for op in ALL_OPERATIONS if op.model_fields["op"].default == op_name)
                if op_name in ("reply", "ask_user"):
                    # Validate the plan-time shape (slots must be $refs), then attach the resolved
                    # values: the "no numeric literals" rule is for the LLM, not for executor output.
                    op_instance = op_cls(**call_dict)
                    op_instance.slots = resolved.get("slots", {})
                else:
                    op_instance = op_cls(**resolved)
                # Savepoint: a failing SQL statement must not poison the rest of the transaction
                async with ctx.conn.begin_nested():
                    envelope: ResultEnvelope = await handler(op_instance, ctx)
            except Exception as e:
                logger.exception("Error executing step %s (%s)", bind_var, op_name)
                sess.reset()
                if isinstance(e, ValidationError):
                    detail = "; ".join(err["msg"] for err in e.errors())
                else:
                    detail = str(e)
                return {"type": "error", "message": f"Step '{op_name}' failed: {detail}"}

            status = envelope.status
            data = envelope.data if isinstance(envelope.data, dict) else {}

            needs_user = (
                status in ("needs_input", "party_ambiguous")
                or (status == "not_found" and op_name == "find_party")
            )
            if status == "not_found" and op_name == "find_party":
                # Offering "add as new party" only makes sense if a later step writes with this party.
                later_writes = any(st["call"]["op"] in WRITE_OPS for st in sess.plan[sess.step_index + 1:])
                if not later_writes:
                    name = resolved.get("name")
                    sess.reset()
                    return {"type": "party_not_found", "queried_name": name}

            if needs_user:
                if op_name == "find_party":
                    data = {**data, "queried_name": resolved.get("name")}
                sess.awaiting = {"step_index": sess.step_index, "bind": bind_var, "op": op_name,
                                 "status": status, "issue": data}
                return {"type": "clarification_needed", "issue": status, "data": data}

            if status in ("error", "invalid", "conflict"):
                sess.reset()
                return {"type": "error", "message": envelope.error or data.get("message") or "Operation failed."}

            # ok / party_found / not_found(for reads): bind and continue
            sess.state[bind_var] = data
            sess.step_index += 1

        result = self._format_final_response(sess)
        sess.reset()                              # request finished: forget its decisions
        return result

    def _format_final_response(self, sess: Session) -> Dict[str, Any]:
        """Flowchart: Select Appropriate Template, else Select Table View."""
        if not sess.plan:
            return {"type": "text", "message": "Done."}
        # Several read steps (e.g. balance + transactions for "info on Ramesh"): show each result
        show_ops = ("get_outstanding", "list_transactions", "list_all_dues", "aggregate", "list_reminders")
        shown = [st for st in sess.plan if st["call"]["op"] in show_ops]
        if len(shown) > 1:
            return {"type": "multi", "results": [
                {"type": "table_view" if st["call"]["op"] in ("aggregate", "list_transactions", "list_all_dues")
                 else "template", "op": st["call"]["op"],
                 "data": sess.state.get(st["bind"], {}), "call": st["call"]} for st in shown]}

        # `reply` / `ask_user` carry no data of their own: present the last real result instead
        steps = [st for st in sess.plan if st["call"]["op"] not in ("reply", "ask_user")] or sess.plan
        last = steps[-1]
        last_op = last["call"]["op"]
        result_data = sess.state.get(last["bind"], {})
        kind = "table_view" if last_op in ("aggregate", "list_transactions", "list_all_dues") else "template"
        return {"type": kind, "op": last_op, "data": result_data, "call": last["call"]}
