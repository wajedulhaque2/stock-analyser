from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

import requests


class OllamaInterpretationError(RuntimeError):
    pass


@dataclass
class InterpretationResult:
    payload: dict[str, Any]
    valid_evidence_ids: list[str]
    invalid_evidence_ids: list[str]
    grounded: bool


MANAGEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "growth_confidence": {"type": "string", "enum": ["POSITIVE", "NEUTRAL", "CAUTIOUS", "UNCLEAR"]},
        "capital_intensity": {"type": "string", "enum": ["INCREASING", "STABLE", "NORMALISING", "UNCLEAR"]},
        "margin_outlook": {"type": "string", "enum": ["IMPROVING", "STABLE", "PRESSURED", "UNCLEAR"]},
        "monetisation_progress": {"type": "string", "enum": ["IMPROVING", "STABLE", "EARLY", "UNCLEAR"]},
        "key_debate": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "why_it_matters": {"type": "string"},
                "bull_implication": {"type": "string"},
                "bear_implication": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
            },
            "required": ["title", "why_it_matters", "bull_implication", "bear_implication", "evidence_ids"],
        },
        "model_watch": {
            "type": "object",
            "properties": {
                "assumption": {"type": "string", "enum": ["REVENUE_GROWTH", "OPERATING_MARGIN", "CASH_CONVERSION", "NONE"]},
                "commentary": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            },
            "required": ["assumption", "commentary", "evidence_ids"],
        },
        "analyst_read": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
    },
    "required": [
        "growth_confidence", "capital_intensity", "margin_outlook", "monetisation_progress",
        "key_debate", "model_watch", "analyst_read", "evidence_ids",
    ],
}


QA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "debates": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "q_index": {"type": "integer"},
                    "topic": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "bull_implication": {"type": "string"},
                    "bear_implication": {"type": "string"},
                },
                "required": ["q_index", "topic", "why_it_matters", "bull_implication", "bear_implication"],
            },
        }
    },
    "required": ["debates"],
}


def _base_url(value: str) -> str:
    text = (value or "http://localhost:11434").strip().rstrip("/")
    if not text.startswith(("http://", "https://")):
        text = "http://" + text
    return text


def list_ollama_models(base_url: str = "http://localhost:11434", timeout: float = 4.0, session: requests.Session | None = None) -> list[str]:
    s = session or requests.Session()
    try:
        response = s.get(_base_url(base_url) + "/api/tags", timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise OllamaInterpretationError(f"Could not reach local Ollama: {exc}") from exc
    models = []
    for row in payload.get("models", []) if isinstance(payload, dict) else []:
        name = str(row.get("name") or row.get("model") or "").strip()
        if name:
            models.append(name)
    return models


def _compact(text: str, limit: int = 850) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


def select_management_evidence(topic_hits: list[dict[str, Any]], max_items: int = 6) -> list[dict[str, Any]]:
    """Keep at most one high-signal direct transcript excerpt per research topic."""
    preferred = [
        "Guidance / outlook", "Cash flow / CapEx", "Margins / costs",
        "Growth / demand", "Investment / strategy", "Risks / headwinds",
    ]
    by_topic: dict[str, dict[str, Any]] = {}
    for row in topic_hits:
        topic = str(row.get("topic") or "")
        if not topic or topic in by_topic:
            continue
        by_topic[topic] = row
    chosen = [by_topic[t] for t in preferred if t in by_topic][:max_items]
    if len(chosen) < max_items:
        chosen_topics = {str(x.get("topic") or "") for x in chosen}
        for row in topic_hits:
            topic = str(row.get("topic") or "")
            if row in chosen or topic in chosen_topics:
                continue
            chosen.append(row)
            chosen_topics.add(topic)
            if len(chosen) >= max_items:
                break
    evidence = []
    for i, row in enumerate(chosen, start=1):
        evidence.append({
            "id": f"E{i}",
            "topic": str(row.get("topic") or "Other"),
            "speaker": str(row.get("speaker") or "Unknown"),
            "excerpt": _compact(str(row.get("excerpt") or ""), 850),
        })
    return evidence


QA_MATERIAL_TERMS = {
    "capex": 5, "capital": 2, "infrastructure": 4, "margin": 4, "expense": 3,
    "guidance": 5, "revenue": 3, "growth": 3, "monet": 4, "advert": 3,
    "ai": 3, "return": 3, "cash flow": 5, "free cash": 5, "spend": 3,
    "demand": 3, "pricing": 3, "competition": 2, "regulat": 2,
}


def select_material_qa(pairs: list[dict[str, Any]], max_items: int = 3) -> list[dict[str, Any]]:
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, row in enumerate(pairs, start=1):
        combined = f"{row.get('question', '')} {row.get('answer', '')}".lower()
        score = sum(weight for term, weight in QA_MATERIAL_TERMS.items() if term in combined)
        scored.append((score, idx, row))
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen = scored[:max_items]
    out = []
    for display_index, (_, original_index, row) in enumerate(chosen, start=1):
        out.append({
            "q_index": display_index,
            "source_index": original_index,
            "analyst": str(row.get("analyst") or "Analyst"),
            "executive": str(row.get("executive") or "Management"),
            "question": _compact(str(row.get("question") or ""), 700),
            "answer": _compact(str(row.get("answer") or ""), 900),
        })
    return out


def interpretation_cache_key(company_key: str, event_key: str, model: str, evidence: list[dict[str, Any]], kind: str) -> str:
    blob = json.dumps({"v": 1, "company": company_key, "event": event_key, "model": model, "kind": kind, "evidence": evidence}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _chat_json(
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    timeout: float = 90.0,
    num_predict: int = 800,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    s = session or requests.Session()
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "format": schema,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": 0.0,
            "num_ctx": 4096,
            "num_predict": num_predict,
        },
    }
    try:
        response = s.post(_base_url(base_url) + "/api/chat", json=payload, timeout=timeout)
        # Some older Ollama/model combinations reject the think field. Retry once without it.
        if response.status_code == 400 and "think" in response.text.lower():
            payload.pop("think", None)
            response = s.post(_base_url(base_url) + "/api/chat", json=payload, timeout=timeout)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise OllamaInterpretationError(f"Local interpretation request failed: {exc}") from exc
    content = (((body or {}).get("message") or {}).get("content") or "").strip()
    if not content:
        raise OllamaInterpretationError("Ollama returned an empty structured response.")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise OllamaInterpretationError(f"Ollama returned malformed structured JSON: {exc}") from exc


def _all_evidence_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for value in payload.get("evidence_ids", []) or []:
        ids.append(str(value))
    for section in [payload.get("key_debate") or {}, payload.get("model_watch") or {}]:
        for value in section.get("evidence_ids", []) or []:
            ids.append(str(value))
    return ids


def validate_management_payload(payload: dict[str, Any], evidence: list[dict[str, Any]]) -> InterpretationResult:
    allowed = {str(x.get("id")) for x in evidence}
    referenced = _all_evidence_ids(payload)
    valid = sorted({x for x in referenced if x in allowed})
    invalid = sorted({x for x in referenced if x not in allowed})
    # Directional interpretation is shown only when at least two direct source excerpts are referenced
    # and the model did not invent evidence identifiers.
    grounded = len(valid) >= 2 and not invalid
    return InterpretationResult(payload=payload, valid_evidence_ids=valid, invalid_evidence_ids=invalid, grounded=grounded)


def interpret_management(
    base_url: str,
    model: str,
    company_name: str,
    ticker: str,
    period: str,
    evidence: list[dict[str, Any]],
    model_facts: dict[str, str],
    timeout: float = 90.0,
    session: requests.Session | None = None,
) -> InterpretationResult:
    if not evidence:
        raise OllamaInterpretationError("No management transcript excerpts were available for interpretation.")
    evidence_text = "\n".join(
        f"{row['id']} | {row['topic']} | {row['speaker']}: {row['excerpt']}" for row in evidence
    )
    facts_text = "\n".join(f"- {k}: {v}" for k, v in model_facts.items())
    system = (
        "You are a bounded equity-research interpretation layer. The supplied Fiscal.ai transcript excerpts "
        "and deterministic model facts are the only permitted evidence. Do not use outside knowledge. Do not "
        "invent or recalculate financial numbers. Do not recommend BUY, SELL, position size, or change a model "
        "assumption. Classify language conservatively; use UNCLEAR when the evidence is not explicit. Every "
        "directional conclusion must cite one or more supplied evidence IDs. Keep all prose concise."
    )
    user = f"""Company: {company_name} ({ticker})\nEarnings period: {period}\n\nDIRECT TRANSCRIPT EVIDENCE\n{evidence_text}\n\nDETERMINISTIC MODEL FACTS (context only; do not alter)\n{facts_text}\n\nReturn the requested structured interpretation. Focus on management signals, the single most important investment debate, and which existing DCF assumption deserves human review. Do not produce new numeric forecasts."""
    payload = _chat_json(base_url, model, system, user, MANAGEMENT_SCHEMA, timeout=timeout, num_predict=750, session=session)
    return validate_management_payload(payload, evidence)


def validate_qa_payload(payload: dict[str, Any], qa_evidence: list[dict[str, Any]]) -> dict[str, Any]:
    allowed = {int(x["q_index"]): x for x in qa_evidence}
    clean = []
    for row in payload.get("debates", []) or []:
        try:
            idx = int(row.get("q_index"))
        except (TypeError, ValueError):
            continue
        if idx not in allowed:
            continue
        clean.append({
            "q_index": idx,
            "analyst": allowed[idx]["analyst"],
            "executive": allowed[idx]["executive"],
            "topic": _compact(row.get("topic", ""), 120),
            "why_it_matters": _compact(row.get("why_it_matters", ""), 260),
            "bull_implication": _compact(row.get("bull_implication", ""), 240),
            "bear_implication": _compact(row.get("bear_implication", ""), 240),
        })
    return {"debates": clean, "grounded": bool(clean)}


def interpret_qa_debates(
    base_url: str,
    model: str,
    company_name: str,
    ticker: str,
    period: str,
    qa_evidence: list[dict[str, Any]],
    timeout: float = 90.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    if not qa_evidence:
        raise OllamaInterpretationError("No analyst Q&A pairs were available for interpretation.")
    evidence_text = "\n\n".join(
        f"Q{row['q_index']} | Analyst {row['analyst']} -> {row['executive']}\nQuestion: {row['question']}\nManagement response: {row['answer']}"
        for row in qa_evidence
    )
    system = (
        "You are classifying already-retrieved earnings-call Q&A for an equity-research dashboard. Use only the "
        "supplied question and management response. Do not use outside knowledge, invent numbers, or recommend "
        "a trade. For each supplied Q-index, identify the investment debate and concise bull/bear implications."
    )
    user = f"Company: {company_name} ({ticker})\nPeriod: {period}\n\n{evidence_text}\n\nReturn one compact debate entry for each Q-index supplied."
    payload = _chat_json(base_url, model, system, user, QA_SCHEMA, timeout=timeout, num_predict=700, session=session)
    return validate_qa_payload(payload, qa_evidence)
