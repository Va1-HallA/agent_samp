"""CLI entry point (Multi-Agent mode):

    python -m scripts.run_agent

Credentials: standard boto3 chain. Run ``aws configure`` first, or export
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN.
"""
import asyncio

import config
from agents.coordinator import Coordinator
from core.llm_backend import BedrockBackend
from core.memory import ChatMemory


async def main():
    llm = BedrockBackend(region=config.AWS_REGION, timeout=config.LLM_TIMEOUT)
    coordinator = Coordinator(llm=llm, model=config.BEDROCK_MODEL_ID)
    memory = ChatMemory(llm=llm, model=config.BEDROCK_MODEL_ID)

    print(f"CareAgent started (model={config.BEDROCK_MODEL_ID})")
    print("Enter a question to begin. Type quit/exit to leave.\n")

    while True:
        try:
            query = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye")
            break
        if not query:
            continue
        if query.lower() in ("quit", "exit"):
            break

        answer = await coordinator.run(query, memory.get_history())
        memory.add_turn("user", query)
        memory.add_turn("assistant", answer)
        memory.compress_if_needed()
        print(f"\nAgent:\n{answer}\n")


if __name__ == "__main__":
    asyncio.run(main())
