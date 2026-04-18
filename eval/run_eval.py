"""Offline evaluation.

Input: eval/testset.jsonl, one JSON object per line:
    {id, question, expected_sources: [...], must_have: [...]}

Metrics:
    - Retrieval: Recall@K against expected_sources.
    - End-to-end: keyword_hit_rate, non_refusal_rate, avg/p95 latency.

Usage:
    python -m eval.run_eval
    python -m eval.run_eval --only r1,r3
    python -m eval.run_eval --skip-e2e
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import anthropic

import config
from agents.coordinator import Coordinator
from core.guardrails import SAFE_FALLBACK
from services.knowledge_service import KnowledgeService


TESTSET_PATH = Path(__file__).resolve().parent / "testset.jsonl"


def load_testset(path: Path = TESTSET_PATH) -> list[dict]:
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


# ---------- Retrieval ----------

def eval_retrieval(cases: list[dict], top_k: int = 5) -> dict:
    ks = KnowledgeService()
    total = 0
    recall_sum = 0.0
    per_case = []
    for c in cases:
        expected = set(c.get("expected_sources") or [])
        if not expected:
            continue
        hits = ks.search_protocol(c["question"], top_k=top_k)
        hit_sources = set()
        for h in hits:
            src = h.get("source", "")
            # Strip chunk suffix; e.g. "foo.txt@123" -> "foo.txt".
            base = src.split("@")[0].split("#")[0]
            hit_sources.add(base)
        matched = expected & hit_sources
        recall = len(matched) / len(expected)
        per_case.append({
            "id": c["id"],
            "recall": round(recall, 3),
            "expected": sorted(expected),
            "hit_sources": sorted(hit_sources),
        })
        recall_sum += recall
        total += 1

    return {
        "top_k": top_k,
        "n": total,
        "recall_at_k": round(recall_sum / total, 3) if total else None,
        "per_case": per_case,
        "fallback_mode": ks.is_using_fallback(),
    }


# ---------- End-to-end ----------

async def eval_end_to_end(cases: list[dict]) -> dict:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    coordinator = Coordinator(client=client, model=config.MODEL_NAME)

    per_case = []
    latencies: list[float] = []
    kw_hits = 0
    refusals = 0
    checked = 0
    for c in cases:
        must_have = c.get("must_have") or []
        t0 = time.time() * 1000
        try:
            answer = await coordinator.run(c["question"])
        except Exception as e:
            answer = f"[RUN ERROR] {type(e).__name__}: {e}"
        elapsed = round(time.time() * 1000 - t0, 1)
        latencies.append(elapsed)

        refused = answer.startswith(SAFE_FALLBACK)
        if refused:
            refusals += 1

        if must_have:
            checked += 1
            if all(k in answer for k in must_have):
                kw_hits += 1

        per_case.append({
            "id": c["id"],
            "latency_ms": elapsed,
            "refused": refused,
            "kw_hit": all(k in answer for k in must_have) if must_have else None,
            "answer_preview": (answer or "")[:120].replace("\n", " "),
        })

    lat_sorted = sorted(latencies) if latencies else []
    return {
        "n": len(cases),
        "keyword_hit_rate": round(kw_hits / checked, 3) if checked else None,
        "non_refusal_rate": round(1 - refusals / len(cases), 3) if cases else None,
        "avg_latency_ms": round(statistics.mean(lat_sorted), 1) if lat_sorted else None,
        "p95_latency_ms": lat_sorted[int(len(lat_sorted) * 0.95) - 1] if len(lat_sorted) >= 2 else None,
        "per_case": per_case,
    }


# ---------- Entry point ----------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="only run the given ids, comma-separated")
    parser.add_argument("--skip-e2e", action="store_true", help="retrieval only")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    cases = load_testset()
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    print(f"loaded {len(cases)} cases")

    print("\n[Retrieval]")
    retr = eval_retrieval(cases, top_k=args.top_k)
    print(json.dumps(retr, ensure_ascii=False, indent=2))

    if args.skip_e2e:
        return

    print("\n[End-to-End]")
    e2e = asyncio.run(eval_end_to_end(cases))
    print(json.dumps(e2e, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
