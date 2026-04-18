"""Protocol agent: knowledge-retrieval tool only."""
import anthropic

from agents.base_agent import BaseAgent
from agents.tool_registry import ToolRegistry
from core.prompt_template import PROTOCOL_PROMPT
from core import tools as T
from services.knowledge_service import KnowledgeService


def create_protocol_agent(
    client: anthropic.Anthropic,
    model: str,
    knowledge: KnowledgeService | None = None,
    max_tokens: int = 2048,
    max_steps: int = 6,
) -> BaseAgent:
    knowledge = knowledge or KnowledgeService()
    registry = ToolRegistry()

    schema = T.SCHEMAS["search_knowledge_base"]
    registry.register(
        "search_knowledge_base",
        T.make_search_knowledge(knowledge),
        schema["description"],
        schema["input_schema"],
    )

    return BaseAgent(
        name="ProtocolAgent",
        system_prompt=PROTOCOL_PROMPT,
        registry=registry,
        client=client,
        model=model,
        max_tokens=max_tokens,
        max_steps=max_steps,
    )
