import json

from stock_analyser.local_interpretation import (
    interpret_management,
    interpret_qa_debates,
    list_ollama_models,
    select_management_evidence,
    select_material_qa,
    validate_management_payload,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, get_payload=None, post_payload=None):
        self.get_payload = get_payload or {}
        self.post_payload = post_payload or {}
        self.last_post = None

    def get(self, url, timeout=None):
        return FakeResponse(self.get_payload)

    def post(self, url, json=None, timeout=None):
        self.last_post = json
        return FakeResponse(self.post_payload)


def test_lists_ollama_models():
    s = FakeSession(get_payload={"models": [{"name": "qwen3.5:2b"}, {"name": "embeddinggemma:latest"}]})
    assert list_ollama_models(session=s) == ["qwen3.5:2b", "embeddinggemma:latest"]


def test_select_management_evidence_one_per_topic():
    hits = [
        {"topic": "Guidance / outlook", "speaker": "CFO", "excerpt": "We expect growth."},
        {"topic": "Guidance / outlook", "speaker": "CFO", "excerpt": "Duplicate topic."},
        {"topic": "Cash flow / CapEx", "speaker": "CFO", "excerpt": "Capital spending remains elevated."},
        {"topic": "Margins / costs", "speaker": "CFO", "excerpt": "Infrastructure costs increased."},
    ]
    out = select_management_evidence(hits)
    assert [x["id"] for x in out] == ["E1", "E2", "E3"]
    assert len({x["topic"] for x in out}) == 3


def test_management_grounding_rejects_invented_evidence_id():
    evidence = [{"id": "E1"}, {"id": "E2"}]
    payload = {
        "evidence_ids": ["E1", "E9"],
        "key_debate": {"evidence_ids": ["E2"]},
        "model_watch": {"evidence_ids": []},
    }
    result = validate_management_payload(payload, evidence)
    assert not result.grounded
    assert result.invalid_evidence_ids == ["E9"]


def test_management_interpretation_uses_think_false_and_no_embeddings():
    content = {
        "growth_confidence": "POSITIVE",
        "capital_intensity": "INCREASING",
        "margin_outlook": "PRESSURED",
        "monetisation_progress": "IMPROVING",
        "key_debate": {
            "title": "AI returns versus capital intensity",
            "why_it_matters": "Cash conversion depends on investment returns.",
            "bull_implication": "Monetisation scales faster than costs.",
            "bear_implication": "Capital intensity remains elevated.",
            "evidence_ids": ["E1", "E2"],
        },
        "model_watch": {
            "assumption": "CASH_CONVERSION",
            "commentary": "Review the normalization path.",
            "evidence_ids": ["E2"],
        },
        "analyst_read": "Growth remains strong while capital intensity is elevated.",
        "evidence_ids": ["E1", "E2"],
    }
    s = FakeSession(post_payload={"message": {"content": json.dumps(content)}})
    evidence = [
        {"id": "E1", "topic": "Growth / demand", "speaker": "CEO", "excerpt": "Demand is strong."},
        {"id": "E2", "topic": "Cash flow / CapEx", "speaker": "CFO", "excerpt": "Infrastructure spend is increasing."},
    ]
    result = interpret_management(
        "http://localhost:11434", "qwen3.5:2b", "Example", "EX", "Q2 2026", evidence,
        {"Base DCF fair value": "$100"}, session=s,
    )
    assert result.grounded
    assert s.last_post["think"] is False
    assert "/api/embed" not in str(s.last_post)
    assert s.last_post["options"]["num_ctx"] == 4096


def test_select_material_qa_prioritises_capex_and_guidance():
    pairs = [
        {"analyst": "A", "executive": "CEO", "question": "General question", "answer": "General response"},
        {"analyst": "B", "executive": "CFO", "question": "How should we think about capex guidance and infrastructure?", "answer": "Capital spending remains elevated."},
        {"analyst": "C", "executive": "CEO", "question": "What about AI monetization?", "answer": "We see monetization progress."},
    ]
    out = select_material_qa(pairs, max_items=2)
    assert out[0]["analyst"] == "B"
    assert len(out) == 2


def test_qa_interpretation_maps_only_supplied_indices():
    response = {"debates": [
        {"q_index": 1, "topic": "CapEx returns", "why_it_matters": "DCF cash conversion", "bull_implication": "Returns scale", "bear_implication": "Spend stays high"},
        {"q_index": 99, "topic": "Invented", "why_it_matters": "x", "bull_implication": "x", "bear_implication": "x"},
    ]}
    s = FakeSession(post_payload={"message": {"content": json.dumps(response)}})
    qa = [{"q_index": 1, "analyst": "A", "executive": "CFO", "question": "Capex?", "answer": "Elevated."}]
    result = interpret_qa_debates("http://localhost:11434", "qwen3.5:2b", "Example", "EX", "Q2", qa, session=s)
    assert result["grounded"]
    assert len(result["debates"]) == 1
    assert result["debates"][0]["analyst"] == "A"
