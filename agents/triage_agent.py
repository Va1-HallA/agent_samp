"""Triage agent: data-lookup + severity assessment tools only."""
from agents.base_agent import BaseAgent
from agents.tool_registry import ToolRegistry
from core.llm_backend import LLMBackend
from core.prompt_template import TRIAGE_PROMPT
from core import tools as T
from services.health_service import HealthService


def create_triage_agent(
    llm: LLMBackend,
    model: str,
    health: HealthService | None = None,
    max_tokens: int = 2048,
    max_steps: int = 8,
) -> BaseAgent:
    health = health or HealthService()
    registry = ToolRegistry()

    for name, func_factory in [
        ("query_resident_info", T.make_query_resident_info),
        ("query_health_records", T.make_query_health_records),
        ("assess_severity", T.make_assess_severity),
    ]:
        schema = T.SCHEMAS[name]
        registry.register(name, func_factory(health),
                          schema["description"], schema["input_schema"])

    return BaseAgent(
        name="TriageAgent",
        system_prompt=TRIAGE_PROMPT,
        registry=registry,
        llm=llm,
        model=model,
        max_tokens=max_tokens,
        max_steps=max_steps,
    )
