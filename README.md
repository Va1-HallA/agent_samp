# CareAgent

A Multi-Agent care assistant that handles the exception-response workflow in an elder-care setting.
When a caregiver reports an anomaly (e.g. "Mr. Zhang's blood pressure jumped to 180/110"), the system:

1. **Triage** — looks up health records, compares against the baseline, and rates severity.
2. **Protocol retrieval** — searches the care knowledge base (RAG) for the matching handling procedure.
3. **Merge & act** — combines both into a recommendation, creates an alert record, and notifies the on-duty nurse.

These tasks need different tools and different system prompts, so they run as separate agents coordinated by a `Coordinator`.

---

## Architecture

Layered architecture:

```
Presentation   ── api/server.py               HTTP + SSE streaming
Application    ── agents/                     Coordinator + TriageAgent + ProtocolAgent
Domain/Service ── services/                   health_service, knowledge_service, alert_service
Infrastructure ── infra/                      SQLAlchemy models, DB session, RAG (Milvus + ES)
```

```
agent_project/
├── config.py                   # central config (reads .env)
├── docker-compose.yaml         # PostgreSQL + Milvus + Redis
├── alembic/                    # DB migrations
│
├── agents/
│   ├── base_agent.py           # ReAct agent loop
│   ├── coordinator.py          # multi-agent orchestrator (router + merger)
│   ├── triage_agent.py
│   ├── protocol_agent.py
│   └── tool_registry.py
│
├── services/
│   ├── health_service.py       # queries + severity assessment
│   ├── knowledge_service.py    # RAG wrapper
│   └── alert_service.py
│
├── core/
│   ├── tools.py                # tool definitions (JSON Schema + handlers)
│   ├── prompt_template.py      # system prompts (triage/protocol/router/merger)
│   ├── guardrails.py           # regex-based prompt-injection + privacy guards
│   └── memory.py               # conversation memory + summary compression
│
├── infra/
│   ├── models.py               # Resident, HealthRecord, Alert
│   ├── db.py
│   └── rag.py                  # BGE embedding + Milvus + Elasticsearch BM25
│
├── api/server.py               # FastAPI endpoint, SSE streaming
├── scripts/
│   ├── seed_data.py            # demo residents + health records
│   ├── build_knowledge_index.py
│   └── run_agent.py            # CLI client
└── eval/testset.jsonl          # evaluation cases
```

---

## Setup

```bash
# 1. infra
docker compose up -d            # PostgreSQL + Milvus + Redis

# 2. deps
pip install -r requirements.txt

# 3. env
cp .env.example .env            # set ANTHROPIC_API_KEY, DB URL, etc.

# 4. DB
alembic upgrade head
python scripts/seed_data.py

# 5. knowledge index
python scripts/build_knowledge_index.py

# 6. run
python scripts/run_agent.py     # CLI
# or
uvicorn api.server:app --reload # HTTP/SSE server
```

---

## Key design choices

- **Router + merger pattern.** The Coordinator first routes to `triage`, `protocol`, `both`, or `direct`; when both agents fire it merges their outputs with an LLM call.
- **Auto-alert on high severity.** The Coordinator inspects the triage result for `severity: high` and writes an `Alert` with `alert_type="vital_signs_abnormal"` automatically.
- **Guardrails.** Regex-based input filter blocks prompt-injection patterns and PII/credential-leak requests; every agent wraps its LLM call with a `SAFE_FALLBACK` message on violation.
- **Memory.** Per-session conversation memory; when over the token budget, earlier turns are compressed with an LLM summary prompt.
- **English-only prompts/data.** System prompts, tool descriptions, error messages and seed data are all in English for portability.

---

## Evaluation

`eval/testset.jsonl` contains labelled cases with `expected_route`, `must_have` keywords, and `expected_sources`. Run with:

```bash
python -m eval.run_eval
```

---

## Status

Experimental. Intended as an interview-prep reference implementation of a production-shaped Multi-Agent system on top of a RAG backend.
