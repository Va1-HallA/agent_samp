"""Short-term chat memory with summarisation.

Persists to a session store (see infra.session_store); this class is just the
in-process rolling buffer + summariser.
"""
from core.llm_backend import LLMBackend, LLMError


class ChatMemory:
    # Cap on rolled-up summary length to prevent unbounded growth.
    MAX_SUMMARY_CHARS = 2000

    def __init__(self, llm: LLMBackend, model: str, max_turns: int = 20):
        self.llm = llm
        self.model = model
        self.max_turns = max_turns
        self.summary = ""
        self.messages = []

    def add_turn(self, role: str, content):
        self.messages.append({"role": role, "content": content})

    def get_history(self) -> list[dict]:
        history = []
        if self.summary:
            history.append({"role": "user", "content": f"[Prior conversation summary]: {self.summary}"})
            history.append({"role": "assistant", "content": "Understood. I have the prior context."})
        if len(self.messages) > self.max_turns:
            history.extend(self.messages[-self.max_turns:])
        else:
            history.extend(self.messages)
        return history

    def compress_if_needed(self):
        if len(self.messages) <= self.max_turns:
            return
        old_messages = self.messages[:]
        self.messages.clear()

        try:
            response = self.llm.create(
                model=self.model,
                max_tokens=512,
                system="Summarize the key points of the following conversation (names, data, conclusions) in under 300 words.",
                messages=old_messages,
            )
        except LLMError:
            # Compression is best-effort; if it fails, keep the tail of the
            # original messages so the conversation is not lost.
            self.messages = old_messages[-self.max_turns:]
            return

        new_summary = ""
        for block in response.content:
            if block.type == "text":
                new_summary = block.text or ""
                break

        combined = f"{self.summary}\n{new_summary}" if self.summary else new_summary
        if len(combined) > self.MAX_SUMMARY_CHARS:
            combined = combined[-self.MAX_SUMMARY_CHARS:]
        self.summary = combined
