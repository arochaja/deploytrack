"""Demo data, offline AI and the live (OpenAI-style) AI path against a fake Purple server.

Run:  .venv/bin/python tests/test_ai_demo.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "demo.db")
os.environ["DEMO_MODE"] = "true"
os.environ["EMAIL_MODE"] = "outbox"
os.environ["API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import ai, config  # noqa: E402
from app.main import app  # noqa: E402


def test_demo_and_offline_ai():
    with TestClient(app) as c:
        meta = c.get("/api/meta").json()
        assert meta["demo_db"] and meta["ai"]["provider"] == "offline"
        assert c.get("/api/summary").json()["total"] > 200          # auto-seeded on first start
        star = c.post("/api/demo/reset").json()["showcase"]

        # no real-looking emails in demo data
        rows = c.get("/api/deployments?limit=5000").json()
        assert all(r["user_email"].endswith("@example.edu") for r in rows if r["user_email"])
        assert any(r["status"] == "Scheduled" for r in rows)

        a = c.post("/api/ai/ask", json={"question": "Who in phase 2 hasn't booked yet?"}).json()
        assert a["provider"] == "offline" and "haven't booked" in a["answer"]

        f = c.post(f"/api/ai/followup/{star}").json()
        assert f["subject"] and "Tuesdays and Thursdays" in f["body"]
        assert c.post(f"/api/deployments/{star}/email", json=f).status_code == 200

        r = c.post(f"/api/ai/reply/{star}", json={"text": "Out sick last week, could we do Thursday at 2pm?"}).json()
        assert r["suggested_status"] == "Scheduled" and "T14:00" in r["scheduled_start"]
        d = c.post(f"/api/ai/reply/{star}/apply", json={
            "reply_text": "Thursday 2pm?", "status": r["suggested_status"], "scheduled_start": r["scheduled_start"],
            "note": r["note"], "reply_subject": r["reply_subject"], "reply_body": r["reply_body"]}).json()
        assert d["status"] == "Scheduled"
        subjects = [e["subject"] for e in c.get(f"/api/deployments/{star}").json()["emails"]]
        assert subjects[0].startswith("You're booked") and not any(s.startswith("Confirmed") for s in subjects)

        r = c.post(f"/api/ai/reply/{star}", json={"text": "I'm retiring at the end of the month"}).json()
        assert r["suggested_status"] == "No Replace - Retired" and r["needs_human"]


def test_live_ai_path(monkeypatch=None):
    calls = []

    class FakeResp:
        def __init__(self, payload):
            self.status_code, self._p, self.text = 200, payload, json.dumps(payload)

        def json(self):
            return self._p

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        last = json["messages"][-1]["content"]
        content = ('{"summary":"Wants Friday","suggested_status":"Scheduled","scheduled_start":"2026-12-04T10:00:00",'
                   '"note":"n","reply_subject":"s","reply_body":"b","needs_human":false,"reason":"r"}'
                   if "Return only JSON" in last else "There are 3 people waiting: Ann (D101).")
        return FakeResp({"choices": [{"message": {"content": content}}]})

    saved = (config.AI_PROVIDER, config.AI_BASE_URL, config.AI_TOKEN, config.AI_MODEL, ai.httpx.post)
    config.AI_PROVIDER, config.AI_BASE_URL, config.AI_TOKEN, config.AI_MODEL = "openai", "https://purple.test/api/v1", "tok", "gpt-x"
    ai.httpx.post = fake_post
    try:
        with TestClient(app) as c:
            a = c.post("/api/ai/ask", json={"question": "who is waiting?"}).json()
            assert a["answer"].startswith("There are 3") and a["provider"] == "openai"
            url, payload, headers = calls[-1]
            assert url == "https://purple.test/api/v1/chat/completions"
            assert headers["Authorization"] == "Bearer tok" and payload["model"] == "gpt-x"
            assert "DEPLOYMENTS:" in payload["messages"][1]["content"]
            assert "old_device_sn" not in payload["messages"][1]["content"]
            dep_id = c.get("/api/deployments?status=Needs Scheduling").json()[0]["id"]
            r = c.post(f"/api/ai/reply/{dep_id}", json={"text": "Friday 10am works"}).json()
            assert r["suggested_status"] == "Scheduled"
            # not configured -> clear 502 error
            config.AI_TOKEN = ""
            e = c.post("/api/ai/ask", json={"question": "x"})
            assert e.status_code == 502 and "AI_TOKEN" in e.json()["detail"]
    finally:
        config.AI_PROVIDER, config.AI_BASE_URL, config.AI_TOKEN, config.AI_MODEL, ai.httpx.post = saved


def test_claude_path():
    from types import SimpleNamespace as NS
    calls = []

    def create(**kw):
        calls.append(kw)
        if kw.get("output_config", {}).get("format"):
            text = json.dumps({"summary": "Wants Friday", "suggested_status": "Scheduled",
                               "scheduled_start": "2026-12-04T10:00:00", "note": "n", "reply_subject": "s",
                               "reply_body": "b", "needs_human": False, "reason": "r"})
        else:
            text = "4 people are waiting (D101)."
        return NS(stop_reason="end_turn", content=[NS(type="thinking", thinking=""), NS(type="text", text=text)])

    saved = (config.AI_PROVIDER, config.AI_NAME, ai._claude_client)
    config.AI_PROVIDER, config.AI_NAME = "claude", "Claude"
    ai._claude_client = NS(beta=NS(messages=NS(create=create)))
    try:
        with TestClient(app) as c:
            a = c.post("/api/ai/ask", json={"question": "who is waiting?"}).json()
            assert a == {"answer": "4 people are waiting (D101).", "provider": "claude"}
            kw = calls[-1]
            assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
            assert kw["messages"][-1] == {"role": "user", "content": "who is waiting?"}
            assert "DeployTrack assistant" in kw["system"]
            dep_id = c.get("/api/deployments?status=Needs Scheduling").json()[0]["id"]
            r = c.post(f"/api/ai/reply/{dep_id}", json={"text": "Friday 10am"}).json()
            assert r["suggested_status"] == "Scheduled"
            fmt = calls[-1]["output_config"]["format"]
            assert fmt["type"] == "json_schema" and "suggested_status" in fmt["schema"]["required"]
            f = c.post(f"/api/ai/followup/{dep_id}")
            assert f.status_code == 200
    finally:
        config.AI_PROVIDER, config.AI_NAME, ai._claude_client = saved


def test_play_record_and_ai_limit():
    from types import SimpleNamespace as NS
    with TestClient(app) as c:
        a = c.post("/api/demo/play-record").json()["id"]
        b = c.post("/api/demo/play-record").json()["id"]
        da, db_ = c.get(f"/api/deployments/{a}").json(), c.get(f"/api/deployments/{b}").json()
        assert a != b and da["status"] == db_["status"] == "Replace" and da["user_email"].endswith("@example.edu")
        assert c.post(f"/api/deployments/{a}/status", json={"status": "Needs Scheduling"}).status_code == 200

    saved = (config.AI_PROVIDER, ai._claude_client, config.AI_HOURLY_LIMIT_PER_IP)
    config.AI_PROVIDER, config.AI_HOURLY_LIMIT_PER_IP = "claude", 2
    ai._claude_client = NS(beta=NS(messages=NS(create=lambda **kw: NS(stop_reason="end_turn", content=[NS(type="text", text="ok")]))))
    try:
        with TestClient(app) as c:
            h = {"x-forwarded-for": "203.0.113.9"}
            codes = [c.post("/api/ai/ask", json={"question": "q"}, headers=h).status_code for _ in range(3)]
            assert codes == [200, 200, 429], codes
            assert c.post("/api/ai/ask", json={"question": "q"}, headers={"x-forwarded-for": "203.0.113.10"}).status_code == 200
    finally:
        config.AI_PROVIDER, ai._claude_client, config.AI_HOURLY_LIMIT_PER_IP = saved


if __name__ == "__main__":
    test_play_record_and_ai_limit()
    test_demo_and_offline_ai()
    test_live_ai_path()
    test_claude_path()
    print("ok")
