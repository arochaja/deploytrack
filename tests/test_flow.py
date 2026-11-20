"""End-to-end check of the deployment workflow against a temporary database.

Run:  .venv/bin/python -m pytest -q tests   (or: .venv/bin/python tests/test_flow.py)
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["EMAIL_MODE"] = "outbox"
os.environ["API_KEY"] = "test-key"
os.environ["REMINDER_INTERVAL_DAYS"] = "3"
os.environ["MAX_REMINDERS"] = "2"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

H = {"X-API-Key": "test-key"}
CSV = (
    " ID, Phase,REQ#,Tech Assign,Status,Shared Device (Y/N),Deployment Type,Device Assigned SN,"
    "DubSupport Tag (Y/N),Accessories,Device Location,Assigned To (User),Assigned To NetID,"
    "Department Contact,Department,Old Device Returned,Notes,NetbiosName,Last Login,Serial Number\n"
    'T001,2,,Hien,Replace,No,Standard Laptop,ABC123,TRUE,"Dock, Keyboard/Mouse",T13 Hybrid,'
    "Test User,TUSER,Pat Boss,Testing Dept,,,OLD999,NETID\\tuser,OLD999\n"
    "T002,2,,,Verify,No,Standard Desktop,,,,,,,,Testing Dept,,,X1,NETID\\someone,X1\n"
    ",,,,,,,,,,,,,,,,,,,\n"
)


def test_full_flow():
    with TestClient(app) as c:
        assert c.get("/api/meta").status_code == 401  # key required

        r = c.post("/api/import", headers=H, files={"file": ("t.csv", CSV, "text/csv")})
        assert r.json() == {"created": 2, "updated": 0, "skipped": 1}
        d = c.get("/api/deployments/T001", headers=H).json()
        assert d["user_email"] == "tuser@uw.edu" and d["old_device_sn"] == "OLD999"

        # No email -> cannot move to Needs Scheduling
        r = c.post("/api/deployments/T002/status", headers=H, json={"status": "Needs Scheduling"})
        assert r.status_code == 400

        # Needs Scheduling -> scheduling email + ticket to-do
        r = c.post("/api/deployments/T001/status", headers=H, json={"status": "Needs Scheduling"})
        assert r.status_code == 200 and r.json()["ticket_pending"] == 1
        mails = c.get("/api/outbox", headers=H).json()
        assert mails[0]["to_addr"] == "tuser@uw.edu" and "schedule" in mails[0]["subject"].lower()
        todo = c.get("/api/todo", headers=H).json()
        assert any(t["kind"] == "create_ticket" and t["deployment_id"] == "T001" for t in todo)

        # Paste REQ# clears the ticket to-do
        c.patch("/api/deployments/T001", headers=H, json={"req_number": "REQ0001", "dept_contact_email": "boss@uw.edu"})
        assert not any(t["kind"] == "create_ticket" for t in c.get("/api/todo", headers=H).json())

        # Reminders: nothing due now, then 2 reminders, then escalation, then nothing
        assert c.post("/api/reminders/run", headers=H).json()["reminded"] == []
        assert c.post("/api/reminders/run?days_ahead=4", headers=H).json()["reminded"] == ["T001"]
        assert c.post("/api/reminders/run?days_ahead=8", headers=H).json()["reminded"] == ["T001"]
        r = c.post("/api/reminders/run?days_ahead=12", headers=H).json()
        assert r["escalated"] == ["T001"]
        assert c.get("/api/outbox", headers=H).json()[0]["to_addr"] == "boss@uw.edu"
        assert c.post("/api/reminders/run?days_ahead=20", headers=H).json() == {
            "reminded": [], "escalated": [], "needs_contact_email": []}

        # Booking webhook by email -> Scheduled + confirmation
        r = c.post("/api/bookings/webhook", headers=H, json={
            "event": "created", "customer_email": "TUser@uw.edu",
            "start": "2026-12-02T18:00:00Z", "end": "2026-12-02T19:00:00Z"})
        assert r.json()["matched"] == ["T001"]
        d = c.get("/api/deployments/T001", headers=H).json()
        assert d["status"] == "Scheduled" and d["scheduled_start"] == "2026-12-02T18:00:00Z"
        assert d["emails"][0]["subject"].startswith("Confirmed: computer setup on Wednesday, December 2 at 10:00 AM")

        # Cancel -> back to Needs Scheduling with rebook email; book again
        c.post("/api/bookings/webhook", headers=H, json={"event": "cancelled", "customer_email": "tuser@uw.edu"})
        d = c.get("/api/deployments/T001", headers=H).json()
        assert d["status"] == "Needs Scheduling" and "rebook" in d["emails"][0]["subject"]
        c.post("/api/bookings/webhook", headers=H, json={"event": "created", "deployment_id": "T001",
                                                         "start": "2026-12-03T18:00:00Z"})

        # Deployed -> old device returned -> Complete -> close-ticket to-do
        c.post("/api/deployments/T001/status", headers=H, json={"status": "Deployed"})
        todo = c.get("/api/todo", headers=H).json()
        assert any(t["kind"] == "collect_device" for t in todo)
        d = c.patch("/api/deployments/T001", headers=H, json={"old_device_returned": "Yes"}).json()
        assert d["status"] == "Complete" and d["close_ticket_pending"] == 1
        c.post("/api/deployments/T001/ticket-closed", headers=H)
        assert c.get("/api/todo", headers=H).json() == []

        # Notes, search, summary, export
        assert c.post("/api/deployments/T001/notes", headers=H, json={"text": "User asked for 2nd monitor"}).status_code == 200
        assert [x["id"] for x in c.get("/api/deployments?q=monitor", headers=H).json()] == []  # notes field != activity
        assert [x["id"] for x in c.get("/api/deployments?q=tuser", headers=H).json()] == ["T001"]
        s = c.get("/api/summary", headers=H).json()
        assert s["by_status"] == {"Complete": 1, "Verify": 1} and s["percent_done"] == 50.0
        assert "T001" in c.get("/api/export.csv", headers=H).text
        assert c.post("/api/deployments/T001/status", headers=H, json={"status": "Bogus"}).status_code == 400
        assert c.get("/api/deployments/NOPE", headers=H).status_code == 404
        assert c.get("/", headers=H).status_code == 200


if __name__ == "__main__":
    test_full_flow()
    print("ok")
