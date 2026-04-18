"""LLM-facing tool adapters.

Each tool:
    1. Calls a service to fetch structured data.
    2. Formats it into a string (tool_result only accepts string / image).
    3. Contains no business rules -- those live in services/.
"""
from services.health_service import HealthService
from services.knowledge_service import KnowledgeService


# ---------- Tool implementations ----------

def _fmt_profile(profile: dict | None) -> str:
    if not profile:
        return "resident not found"
    return (
        f"Name: {profile['name']}, Age: {profile['age']}, Room: {profile['room']}\n"
        f"Care level: {profile['care_level']}\n"
        f"Chronic conditions: {profile['chronic_conditions']}\n"
        f"Medication: {profile.get('medication') or 'none'}\n"
        f"Diet: {profile.get('diet') or 'none'}\n"
        f"Activity: {profile.get('activity') or 'none'}\n"
        f"Emergency contact: {profile['emergency_contact']}"
    )


def make_query_resident_info(health: HealthService):
    def fn(resident_name: str) -> str:
        return _fmt_profile(health.get_resident_profile(resident_name))
    return fn


def make_query_health_records(health: HealthService):
    def fn(resident_name: str, metric: str | None = None, days: int = 7) -> str:
        profile = health.get_resident_profile(resident_name)
        if not profile:
            return f"resident not found: {resident_name}"
        records = health.get_recent_records(profile["id"], metric, days)
        if not records:
            return f"no {metric or ''} records for {resident_name} in the last {days} days"
        lines = [f"{r['metric']}: {r['value']} ({r['time'][:16]})" for r in records]
        return "\n".join(lines)
    return fn


def make_query_care_plan(health: HealthService):
    def fn(resident_name: str) -> str:
        profile = health.get_resident_profile(resident_name)
        if not profile:
            return f"resident not found: {resident_name}"
        return (
            f"{resident_name} care plan:\n"
            f"Medication: {profile.get('medication') or 'none'}\n"
            f"Diet: {profile.get('diet') or 'none'}\n"
            f"Activity: {profile.get('activity') or 'none'}"
        )
    return fn


def make_assess_severity(health: HealthService):
    def fn(resident_name: str, metric: str, current_value: str) -> str:
        profile = health.get_resident_profile(resident_name)
        if not profile:
            return f"resident not found: {resident_name}"
        severity = health.assess_severity(metric, current_value)
        baseline = health.compute_baseline(profile["id"], metric)
        return (
            f"Severity: {severity}\n"
            f"Current value: {current_value}\n"
            f"7-day baseline: {baseline}"
        )
    return fn


def make_search_knowledge(knowledge: KnowledgeService):
    def fn(query: str, top_k: int = 5) -> str:
        results = knowledge.search_protocol(query, top_k)
        if not results:
            return "no matching protocol found"
        return "\n\n".join(
            f"[source: {r.get('source', 'unknown')}]\n{r['text']}" for r in results
        )
    return fn


# ---------- Tool schemas (exposed to the LLM) ----------

_NAME_PROP = {"type": "string", "description": "Resident name, e.g. 'Mr. Zhang'"}
_METRIC_ENUM = {
    "type": "string",
    "enum": ["blood_pressure", "heart_rate", "blood_glucose", "body_temperature"],
    "description": "Vital sign metric",
}


SCHEMAS = {
    "query_resident_info": {
        "description": "Look up a resident's profile: age, room, chronic conditions, medication, emergency contact.",
        "input_schema": {
            "type": "object",
            "properties": {"resident_name": _NAME_PROP},
            "required": ["resident_name"],
        },
    },
    "query_health_records": {
        "description": "Look up a resident's recent N days of health records (blood pressure, heart rate, etc.), newest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "resident_name": _NAME_PROP,
                "metric": _METRIC_ENUM,
                "days": {"type": "integer", "description": "Number of days to query, default 7", "default": 7},
            },
            "required": ["resident_name"],
        },
    },
    "query_care_plan": {
        "description": "Look up a resident's care plan: current medication, diet, activity schedule.",
        "input_schema": {
            "type": "object",
            "properties": {"resident_name": _NAME_PROP},
            "required": ["resident_name"],
        },
    },
    "assess_severity": {
        "description": "Assess the severity of a current metric value (high/medium/low/normal) and return the 7-day baseline.",
        "input_schema": {
            "type": "object",
            "properties": {
                "resident_name": _NAME_PROP,
                "metric": _METRIC_ENUM,
                "current_value": {
                    "type": "string",
                    "description": "Current value. For blood_pressure pass '180/110'; for others pass a numeric string like '38.5'.",
                },
            },
            "required": ["resident_name", "metric", "current_value"],
        },
    },
    "search_knowledge_base": {
        "description": "Search the care knowledge base for protocols, medication guides, and treatment plans.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords, e.g. 'hypertension emergency protocol'"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
}
