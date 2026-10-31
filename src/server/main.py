import asyncio
from typing import Dict, Any, Optional
from src.server.utils import PromptMaker

class ExecutionState:
    def __init__(self):
        self.plan: list = []
        self.step_index: int = 0
        self.vars: Dict[str, Any] = {}
        self.awaiting_clarification: Optional[Dict] = None

class DukaanOrchestrator:
    def __init__(self, execution_context, prompt_maker: PromptMaker):
        self.ctx = execution_context
        self.prompt_maker = prompt_maker
        self.state = ExecutionState()

    async def handle_input(self, user_input: str, user_answer: bool = False):
        """Main entry point. Handles initial requests and clarification answers."""
        
        # Branch: If this is an answer to a clarification question[cite: 1]
        if user_answer and self.state.awaiting_clarification:
            direct_answer = self._is_direct_answer(user_input, self.state.awaiting_clarification)
            if direct_answer:
                # Directly change the query / resolve the missing variable[cite: 1]
                self._apply_direct_answer(user_input)
                return await self._run_execution_loop()
            else:
                # Update Planning and Action wrt Answer given (Needs LLM)[cite: 1]
                self.state.awaiting_clarification = None
                return await self._replan_with_llm(user_input)

        # Branch: Fresh Request -> LLM -> Planning and Action[cite: 1]
        self.state.plan = await self._call_llm_for_plan(user_input)
        self.state.step_index = 0
        return await self._run_execution_loop()

    async def _run_execution_loop(self):
        """Executes the plan step-by-step, checking for issues at each step."""
        
        # Check: Actions Finished?[cite: 1]
        while self.state.step_index < len(self.state.plan):
            step = self.state.plan[self.state.step_index]
            
            # Run SQL Query via Dispatcher (e.g., execute_find_party, execute_propose_sale)[cite: 1]
            result_envelope = await self._execute_step(step)
            
            # Check: Facing Any Issues? (e.g., ambiguous party, missing fields)[cite: 1]
            if result_envelope.status in ["needs_input", "party_ambiguous", "conflict"]:
                # Ask Question to clarify -> pause execution and wait for User[cite: 1]
                self.state.awaiting_clarification = {
                    "step": step,
                    "issue": result_envelope.data
                }
                return self._format_question_to_user(result_envelope)
                
            elif result_envelope.status == "error":
                # Fatal error, abort plan
                return {"type": "error", "message": result_envelope.error}
                
            # No issues, save state and advance
            self.state.vars[step["bind"]] = result_envelope.data
            self.state.step_index += 1

        # Flow: Actions Finished -> Yes[cite: 1]
        return self._finalize_output()

    def _finalize_output(self) -> Dict[str, Any]:
        """Output Phase: Select Template or Table View based on results."""
        final_result = self.state.vars.get(self.state.plan[-1]["bind"], {})
        
        # Check: Found? (Does the data fit a standard template?)[cite: 1]
        if self._fits_standard_template(final_result):
            # Select Appropriate Template[cite: 1]
            output_text = self._apply_template(final_result)
        else:
            # Select Table View (e.g., for aggregations or long lists)[cite: 1]
            output_text = self._generate_table_view(final_result)
            
        return {"type": "output", "message": output_text}

    # --- Stubs for LLM and Execution Integration ---
    
    async def _call_llm_for_plan(self, user_input: str) -> list:
        """Injects prompt and gets the ExecutionPlan JSON from the LLM."""
        # sys_prompt = self.prompt_maker.build_prompt(...)
        # return parsed_llm_plan.steps
        pass

    async def _replan_with_llm(self, new_input: str) -> list:
        """Calls LLM with previous context + new input to generate an updated plan."""
        pass

    async def _execute_step(self, step: dict):
        """Passes the step to the operations dispatcher (e.g., OPERATION_HANDLERS)."""
        pass

    def _is_direct_answer(self, input_text: str, context: dict) -> bool:
        """Determines if the user's reply directly answers the clarification (e.g., '1' or 'Ramesh')."""
        return True

    def _apply_direct_answer(self, input_text: str):
        """Mutates the current step's parameters based on the direct answer."""
        pass
        
    def _format_question_to_user(self, result_envelope) -> Dict:
        """Formats interactive buttons or text for 'Ask Question to clarify'."""
        pass

    def _fits_standard_template(self, result_data: dict) -> bool:
        return True

    def _apply_template(self, data: dict) -> str:
        return "Template Text"

    def _generate_table_view(self, data: dict) -> str:
        return "Table View"

