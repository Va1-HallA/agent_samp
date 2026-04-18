"""Rule-based guardrails.

Three checkpoints:
    input  -> check_input     (entry: prompt injection, length, privacy abuse)
    tool   -> check_tool_call  (mid: tool whitelist, key param bounds)
    output -> check_output    (exit: PII masking, system-prompt leak)

Design:
    - Regex + whitelist only; no LLM-as-judge.
    - Violations raise GuardrailViolation for upstream handling.
    - Structural checks only -- domain correctness is the agent's job.
"""
from __future__ import annotations

import re


# ============================================================
# Exceptions
# ============================================================

class GuardrailViolation(Exception):
    """Raised when a guardrail blocks a call. stage in {input, tool, output}."""

    def __init__(self, stage: str, reason: str, detail: str = ""):
        super().__init__(f"[{stage}] {reason}: {detail}")
        self.stage = stage
        self.reason = reason
        self.detail = detail


# ============================================================
# Input checks
# ============================================================

_INJECTION_PATTERNS = [
    r"ignore\s+(all|previous|above)\s+(instructions?|prompts?)",
    r"disregard\s+(the|your)\s+(previous|system)",
    r"you\s+are\s+now\s+.{0,20}(developer|admin|root|DAN)",
    r"(print|output|reveal|tell\s+me).{0,20}(system|hidden)\s*prompt",
]

_PRIVACY_PATTERNS = [
    r"(send|export|give\s+me)\s+.*(medical\s+records?|profiles?|ID\s+cards?|phone\s+numbers?)",
    r"(copy|scrape|crawl)\s+(all|every)\s+(resident|user|patient)s?",
]

MAX_INPUT_LEN = 2000


def check_input(query: str) -> None:
    if not isinstance(query, str) or not query.strip():
        raise GuardrailViolation("input", "empty_query", "")

    if len(query) > MAX_INPUT_LEN:
        raise GuardrailViolation(
            "input", "too_long",
            f"len={len(query)} > {MAX_INPUT_LEN}",
        )

    for pat in _INJECTION_PATTERNS:
        if re.search(pat, query, re.IGNORECASE):
            raise GuardrailViolation("input", "prompt_injection", pat)

    for pat in _PRIVACY_PATTERNS:
        if re.search(pat, query, re.IGNORECASE):
            raise GuardrailViolation("input", "privacy_abuse", pat)


# ============================================================
# Tool checks
# ============================================================

# Allowlist is slightly broader than actual registered tools to avoid
# missing updates when factories add new tools; the actual bound is the
# registry the LLM sees.
DEFAULT_TOOL_WHITELIST = {
    "TriageAgent": {
        "query_resident_info",
        "query_health_records",
        "query_care_plan",
        "assess_severity",
    },
    "ProtocolAgent": {
        "search_knowledge_base",
    },
}


def check_tool_call(
    agent_name: str,
    tool_name: str,
    tool_input: dict,
    whitelist: dict[str, set[str]] | None = None,
) -> None:
    wl = whitelist or DEFAULT_TOOL_WHITELIST
    allowed = wl.get(agent_name, set())
    if tool_name not in allowed:
        raise GuardrailViolation(
            "tool", "tool_not_allowed",
            f"{agent_name} cannot call {tool_name}",
        )

    # Only validate strongly-constrained parameters; skip free-text fields.
    if "top_k" in tool_input:
        tk = tool_input["top_k"]
        if not isinstance(tk, int) or tk <= 0 or tk > 50:
            raise GuardrailViolation(
                "tool", "bad_top_k", f"top_k={tk}",
            )

    if "days" in tool_input:
        d = tool_input["days"]
        if not isinstance(d, int) or d <= 0 or d > 365:
            raise GuardrailViolation(
                "tool", "bad_days", f"days={d}",
            )

    if "resident_name" in tool_input:
        rn = tool_input["resident_name"]
        if not isinstance(rn, str) or not rn.strip() or len(rn) > 50:
            raise GuardrailViolation(
                "tool", "bad_resident_name", f"len={len(str(rn))}",
            )

    if "severity" in tool_input:
        if tool_input["severity"] not in {"low", "medium", "high"}:
            raise GuardrailViolation(
                "tool", "bad_severity", str(tool_input["severity"]),
            )


# ============================================================
# Output checks
# ============================================================

# Chinese ID and mobile number patterns -- masked, not blocked.
_ID_CARD_RE = re.compile(r"\b\d{17}[\dXx]\b")
_PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")

# Use only strong literal signals to avoid false positives (e.g., a user
# asking "are you a care agent" should not trip a leak detector).
_SYSTEM_LEAK_PATTERNS = [
    r"<system>",
    r"\[SYSTEM\]",
    r"ROUTER_PROMPT",
    r"MERGE_PROMPT",
    r"TRIAGE_PROMPT",
    r"PROTOCOL_PROMPT",
]


def check_output(text: str) -> str:
    """Return masked text. Raises GuardrailViolation on system-prompt leak."""
    if not isinstance(text, str):
        return text

    for pat in _SYSTEM_LEAK_PATTERNS:
        if re.search(pat, text):
            raise GuardrailViolation("output", "system_prompt_leak", pat)

    masked = _ID_CARD_RE.sub(lambda m: m.group()[:6] + "********" + m.group()[-2:], text)
    masked = _PHONE_RE.sub(lambda m: m.group()[:3] + "****" + m.group()[-4:], masked)
    return masked


# ============================================================
# Safe fallback message
# ============================================================

SAFE_FALLBACK = "Sorry, I cannot answer this question. For emergencies please contact the on-duty nurse or dial 911."
