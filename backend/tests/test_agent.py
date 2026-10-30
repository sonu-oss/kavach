import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.agent import guardian
from app.agent.schemas import AgentIn
from app.complaint import retrieval
from app.llm import LLMError
from app.schemas import AnalyzeResponse
from app.vault import index, store

from tests.test_vectors import fake_embed
from conftest import signed_in

SEXTORTION = {
    "name": "rahul_k_07",
    "platform": "Instagram",
    "messages": [
        {"id": "r1", "sender": "rahul", "text": "10k bhej de UPI pe rahul.k07@okaxis warna tere photos viral kar dunga", "time": "21:23"},
        {"id": "r2", "sender": "me", "text": "please stop", "time": "21:24"},
        {"id": "r3", "sender": "rahul", "text": "call me on 9845012345 or your papa gets them", "time": "21:25"},
    ],
}
SAFE = {"name": "Ananya", "messages": [{"id": "a1", "sender": "ananya", "text": "lunch tomorrow?"}]}


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(retrieval, "_embed_failed", True)
    monkeypatch.setattr(guardian.evidence_index.vectors, "embed_docs", lambda ts: [fake_embed(t) for t in ts])
    monkeypatch.setattr(guardian.evidence_index.vectors, "embed_query", fake_embed)


def run(goal, conversation=None):
    return asyncio.run(guardian.run_agent(AgentIn(goal=goal, conversation=conversation)))


def tools(out):
    return [s["tool"] for s in out["steps"]]


# ---------- without a model: the fixed plan ----------

def test_plan_handles_a_threatening_chat_end_to_end():
    out = run("handle this chat for me", SEXTORTION)
    assert out["source"] == "planned"
    assert tools(out) == ["analyze_chat", "save_to_vault", "find_laws", "draft_complaint"]
    assert all(s["ok"] for s in out["steps"])
    cid = out["saved"]["capture_id"]
    rows = [r for r in store.all_rows() if r["capture_id"] == cid]
    assert len(rows) == 3 and store.verify()["ok"]
    assert json.loads(rows[0]["analysis_json"])["sealed_by"] == "agent"
    assert out["complaint"]["capture_id"] == cid
    assert "1930" in out["answer"]


def test_plan_leaves_a_safe_chat_alone():
    out = run("check this", SAFE)
    assert tools(out) == ["analyze_chat"]
    assert out["saved"] is None and store.all_rows() == []


def test_plan_without_a_chat_searches_evidence():
    store.save_capture(guardian.EvidenceIn(platform="WhatsApp", conversation="x",
                                           messages=[{"ref": "1", "sender": "x", "text": "ninna mane gottu"}]))
    out = run("when did he say he knows my house?")
    assert tools(out) == ["search_evidence"]
    assert out["steps"][0]["results"][0]["text"] == "ninna mane gottu"


def test_plan_suggests_sos_for_high_risk_stalking_but_sends_nothing(monkeypatch):
    async def stalking(_req):
        return AnalyzeResponse(category="stalking", risk_score=90, severity="high", reason="Knows her college.",
                               flagged_message_ids=["r1"], source="keyword_fallback", pii_masked=0)
    monkeypatch.setattr(guardian, "analyze", stalking)
    out = run("help", SEXTORTION)
    assert tools(out)[-1] == "suggest_sos" and out["sos_suggested"]


# ---------- with a model: scripted tool calls ----------

def call(name, args=None, i=0):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}


@pytest.fixture
def model(monkeypatch):
    """Replays a script of model turns and records what the model was sent."""
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    sent = []
    script = []

    async def fake_chat(messages, tools, model):
        sent.append(json.loads(json.dumps(messages)))
        turn = script.pop(0) if script else {"tool_calls": [call("analyze_chat")]}
        if isinstance(turn, Exception):
            raise turn
        return {"role": "assistant", "content": turn.get("content", ""), "tool_calls": turn.get("tool_calls", [])}

    async def fake_analyze(_req):
        return AnalyzeResponse(category="sextortion_blackmail", risk_score=88, severity="high",
                               reason="Demands money, threatens to leak photos.", flagged_message_ids=["r1", "r3"],
                               source="llm", pii_masked=2, model="m")

    async def fake_complaint(capture_id):
        return {"capture_id": capture_id, "complaint": {"subject": "Sextortion"}, "sections": [{"id": "BNS-308"}],
                "pipeline": {"source": "llm"}, "dropped_citations": []}

    monkeypatch.setattr(guardian, "groq_chat", fake_chat)
    monkeypatch.setattr(guardian, "analyze", fake_analyze)
    monkeypatch.setattr(guardian, "build_complaint", fake_complaint)
    return script, sent


def test_agent_runs_the_tools_the_model_chooses(model):
    script, sent = model
    script += [
        {"tool_calls": [call("analyze_chat")]},
        {"tool_calls": [call("save_to_vault", i=1), call("find_laws", {"description": "leak photos for money"}, i=2)]},
        {"tool_calls": [call("draft_complaint", i=3)]},
        {"content": "This is sextortion. I saved it and drafted a complaint."},
    ]
    out = run("handle it", SEXTORTION)
    assert out["source"] == "agent" and out["model"] == config.GROQ_MODEL
    assert tools(out) == ["analyze_chat", "save_to_vault", "find_laws", "draft_complaint"]
    assert out["answer"].startswith("This is sextortion")
    tool_msgs = [m for m in sent[-1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c0", "c1", "c2", "c3"]
    assert json.loads(tool_msgs[1]["content"])["records_sealed"] == 3


def test_model_never_sees_raw_chat_text_or_personal_details(model):
    script, sent = model
    script += [{"tool_calls": [call("analyze_chat")]}, {"content": "done"}]
    run("my number 9845012345 is in it, check this", SEXTORTION)
    seen = json.dumps(sent)
    assert "9845012345" not in seen and "okaxis" not in seen
    assert "viral kar dunga" not in seen  # the chat reaches the model only via analyze_chat's verdict


def test_tools_outside_the_allow_list_are_refused(model):
    script, _ = model
    script += [{"tool_calls": [call("send_telegram", {"to": "everyone"})]}, {"content": "ok"}]
    out = run("tell my friends", SEXTORTION)
    assert out["steps"][0] == {"tool": "send_telegram", "args": {"to": "everyone"}, "ok": False,
                               "summary": "Refused: not an allowed tool"}


def test_wrong_order_and_bad_arguments_come_back_as_errors(model):
    script, sent = model
    script += [
        {"tool_calls": [call("save_to_vault")]},
        {"tool_calls": [{"id": "x", "function": {"name": "find_laws", "arguments": "not json"}}]},
        {"tool_calls": [call("find_laws", {"wrong": 1})]},
        {"content": "ok"},
    ]
    out = run("save it", SEXTORTION)
    assert [s["summary"] for s in out["steps"]] == ["call analyze_chat first", "Bad arguments"]
    assert "not a JSON object" in sent[2][-1]["content"]
    assert store.all_rows() == []


def test_turn_limit_then_the_plan_finishes(model):
    script, sent = model  # empty script: the model asks for analyze_chat forever
    out = run("handle it", SEXTORTION)
    assert len(sent) == guardian.MAX_TURNS
    assert out["source"] == "agent+planned"
    assert tools(out).count("analyze_chat") == guardian.MAX_TURNS  # cached after the first: one real scan
    assert tools(out)[-3:] == ["save_to_vault", "find_laws", "draft_complaint"]


def test_model_failure_mid_run_is_finished_by_the_plan_without_repeating_steps(model):
    script, _ = model
    script += [{"tool_calls": [call("analyze_chat")]}, {"tool_calls": [call("save_to_vault")]}, LLMError("down")]
    out = run("handle it", SEXTORTION)
    assert out["source"] == "agent+planned"
    assert tools(out) == ["analyze_chat", "save_to_vault", "find_laws", "draft_complaint"]
    assert len({r["capture_id"] for r in store.all_rows()}) == 1


def test_saving_twice_seals_once(model):
    script, _ = model
    script += [{"tool_calls": [call("analyze_chat")]}, {"tool_calls": [call("save_to_vault"), call("save_to_vault", i=1)]},
               {"content": "ok"}]
    run("save", SEXTORTION)
    assert len(store.all_rows()) == 3


def test_api_endpoint():
    from app.main import app

    client = signed_in(TestClient(app))
    res = client.post("/agent", json={"goal": "is this dangerous?", "conversation": SAFE})
    assert res.status_code == 200 and res.json()["analysis"]["category"] == "safe"
    assert client.post("/agent", json={"goal": ""}).status_code == 422
