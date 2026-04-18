"""CLI entry point (Multi-Agent mode):

    python -m scripts.run_agent

Type quit/exit or Ctrl+C to leave.
"""
import asyncio

import anthropic

import config
from agents.coordinator import Coordinator
from core.memory import ChatMemory


async def main():
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    coordinator = Coordinator(client=client, model=config.MODEL_NAME)
    memory = ChatMemory(client=client, model=config.MODEL_NAME)

    print(f"CareAgent started (model={config.MODEL_NAME})")
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
