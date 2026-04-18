"""Generic ReAct agent.

- system_prompt / tools are injected via constructor.
- Tool dispatch goes through ToolRegistry (no text parsing; relies on Claude
  native tool_use blocks).
- stop_reason drives the loop: tool_use -> execute -> append tool_result ->
  next turn; end_turn -> return text.
- Guardrails: check_input on entry, check_tool_call on each tool call,
  check_output on exit.
- All LLM calls go through core.llm_client.call_messages (retry + timeout).

Exception strategy:
    GuardrailViolation (input)              -> SAFE_FALLBACK
    anthropic.APIError / unexpected errors  -> SAFE_FALLBACK + reason
    Tool exceptions                          -> fed back to the LLM as text
"""
import logging

import anthropic
from anthropic.types import Message

from agents.tool_registry import ToolRegistry
from core.guardrails import (
    GuardrailViolation,
    SAFE_FALLBACK,
    check_input,
    check_output,
    check_tool_call,
)
from core.llm_client import call_messages

logger = logging.getLogger(__name__)


class BaseAgent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        registry: ToolRegistry,
        client: anthropic.Anthropic,
        model: str,
        max_tokens: int = 2048,
        max_steps: int = 10,
        guardrail_role: str | None = None,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.registry = registry
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        # Identity used for tool-whitelist guardrail checks.
        self.guardrail_role = guardrail_role or name

    def run(self, query: str, chat_history: list[dict] | None = None) -> str:
        try:
            check_input(query)
        except GuardrailViolation as e:
            return f"{SAFE_FALLBACK} (blocked: {e.reason})"

        messages: list[dict] = list(chat_history) if chat_history else []
        messages.append({"role": "user", "content": query})

        try:
            for _ in range(self.max_steps):
                response = self._call_llm(messages)
                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason == "end_turn":
                    raw = self._extract_text(response) or f"[{self.name}] no text output"
                    return self._safe_output(raw)

                if response.stop_reason == "tool_use":
                    tool_results = self._run_tools(response)
                    messages.append({"role": "user", "content": tool_results})
                    continue

                raw = self._extract_text(response) or f"[{self.name}] stopped: {response.stop_reason}"
                return self._safe_output(raw)

            return f"[{self.name}] max_steps exceeded"
        except anthropic.APIError as e:
            logger.exception("LLM API error in %s.run", self.name)
            return f"{SAFE_FALLBACK} (LLM service error: {type(e).__name__})"
        except Exception as e:
            logger.exception("Unexpected error in %s.run", self.name)
            return f"{SAFE_FALLBACK} (internal error: {type(e).__name__})"

    def _call_llm(self, messages: list[dict]) -> Message:
        return call_messages(
            self.client,
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system_prompt,
            tools=self.registry.get_schemas(),
            messages=messages,
        )

    def _run_tools(self, response: Message) -> list[dict]:
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                check_tool_call(self.guardrail_role, block.name, dict(block.input))
                func = self.registry.get_func(block.name)
                out = func(**block.input)
            except GuardrailViolation as e:
                # Feed blocked calls back so the LLM can retry with a legal tool.
                out = f"[guardrail blocked] {e.reason}: {e.detail}"
            except Exception as e:
                # Tool errors are fed back to the LLM instead of aborting.
                out = f"[tool error] {type(e).__name__}: {e}"
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(out),
            })
        return results

    @staticmethod
    def _extract_text(response: Message) -> str | None:
        for block in response.content:
            if block.type == "text":
                return block.text
        return None

    @staticmethod
    def _safe_output(text: str) -> str:
        try:
            return check_output(text)
        except GuardrailViolation:
            return SAFE_FALLBACK


ReActAgent = BaseAgent
