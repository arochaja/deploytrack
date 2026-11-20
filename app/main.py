"""DeployTrack — central tracker for the UWF device refresh.

Run:  .venv/bin/uvicorn app.main:app --reload
UI:   http://127.0.0.1:8000        API docs (for Purple): http://127.0.0.1:8000/docs
"""
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, config, db, demo_data, importer, workflow
from .integrations import send_email

STATIC = Path(__file__).parent / "static"


async def _reminder_loop():
    while True:
        await asyncio.sleep(config.REMINDER_CHECK_MINUTES * 60)
        try:
            with db.session() as conn:
                workflow.run_reminders(conn)
        except Exception as exc:  # never let the loop die
            print("reminder loop error:", exc)


@asynccontextmanager
async def lifespan(_app):
    db.init()
    if config.DEMO_MODE and config.DEMO_DB:
        with db.session() as conn:
            if not conn.execute("SELECT 1 FROM deployments LIMIT 1").fetchone():
                demo_data.seed(conn)
    task = asyncio.create_task(_reminder_loop())
    yield
    task.cancel()


app = FastAPI(
    title="DeployTrack API",
    version="0.1.0",
    description=(
        "Central tracker for the UWF Computer Devices Refresh. Purple can use these endpoints "
        "as tools: look up deployments, summarize progress, change statuses (which triggers "
        "emails, ticket to-dos and reminders) and add notes."
    ),
    lifespan=lifespan,
)


def require_key(x_api_key: Optional[str] = Header(default=None)):
    if config.API_KEY and x_api_key != config.API_KEY:
        raise HTTPException(401, "Missing or invalid X-API-Key header")


def _err(exc: workflow.WorkflowError):
    msg = str(exc)
    raise HTTPException(404 if "not found" in msg else 400, msg)


# ---------------------------------------------------------------- models

class StatusChange(BaseModel):
    status: str = Field(..., description="New status. See GET /api/meta for valid values.")
    reason: str = ""
    actor: str = Field("user", description="Who made the change, e.g. a tech's name or 'purple'.")


class NoteIn(BaseModel):
    text: str
    actor: str = "user"


class DeploymentPatch(BaseModel):
    phase: Optional[str] = None
    req_number: Optional[str] = None
    tech: Optional[str] = None
    deployment_type: Optional[str] = None
    new_device_sn: Optional[str] = None
    accessories: Optional[str] = None
    location: Optional[str] = None
    user_name: Optional[str] = None
    netid: Optional[str] = None
    user_email: Optional[str] = None
    dept_contact: Optional[str] = None
    dept_contact_email: Optional[str] = None
    department: Optional[str] = None
    old_device_sn: Optional[str] = None
    old_device_returned: Optional[str] = None
    notes: Optional[str] = None
    scheduled_start: Optional[str] = None
    scheduled_end: Optional[str] = None
    actor: str = "user"


class BulkStatus(BaseModel):
    ids: List[str]
    status: str
    actor: str = "user"


class BookingEvent(BaseModel):
    event: str = Field("created", description="created | updated | cancelled")
    customer_email: str = ""
    start: str = Field("", description="ISO 8601 start time")
    end: str = ""
    deployment_id: str = Field("", description="Optional: match directly instead of by email")


# ---------------------------------------------------------------- read endpoints

@app.get("/api/meta", dependencies=[Depends(require_key)], tags=["info"])
def meta():
    """Valid statuses and current automation settings."""
    return {
        "statuses": workflow.STATUSES, "pipeline": workflow.PIPELINE,
        "no_replace": workflow.NO_REPLACE, "demo_mode": config.DEMO_MODE,
        "email_mode": config.EMAIL_MODE, "snow_mode": config.SNOW_MODE,
        "redirect_all_email_to": config.REDIRECT_ALL_EMAIL_TO,
        "reminder_interval_days": config.REMINDER_INTERVAL_DAYS,
        "max_reminders": config.MAX_REMINDERS, "bookings_url": config.BOOKINGS_URL,
        "demo_db": config.DEMO_DB, "ai": ai.status(),
    }


@app.get("/api/deployments", dependencies=[Depends(require_key)], tags=["deployments"])
def list_deployments(
    status: Optional[str] = Query(None, description="Exact status, or 'active' / 'no-replace'"),
    phase: Optional[str] = None,
    tech: Optional[str] = None,
    q: Optional[str] = Query(None, description="Search name, NetID, department, serials, REQ#, ID"),
    limit: int = 500,
):
    """Find deployments, e.g. everyone in Phase 2 who still needs to schedule."""
    sql, args = "SELECT * FROM deployments WHERE 1=1", []
    if status == "active":
        sql += f" AND status IN ({','.join('?' * len(workflow.PIPELINE[:-1]))})"
        args += workflow.PIPELINE[:-1]
    elif status == "no-replace":
        sql += " AND status LIKE 'No Replace%'"
    elif status:
        sql += " AND lower(status) = lower(?)"
        args.append(status)
    if phase not in (None, ""):
        sql += " AND phase = ?"
        args.append(phase)
    if tech:
        sql += " AND lower(trim(tech)) = lower(trim(?))"
        args.append(tech)
    if q:
        like = f"%{q.lower()}%"
        sql += (" AND (lower(id) LIKE ? OR lower(user_name) LIKE ? OR lower(netid) LIKE ? OR"
                " lower(department) LIKE ? OR lower(req_number) LIKE ? OR lower(new_device_sn) LIKE ?"
                " OR lower(old_device_sn) LIKE ? OR lower(notes) LIKE ?)")
        args += [like] * 8
    sql += " ORDER BY id LIMIT ?"
    args.append(limit)
    with db.session() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


@app.get("/api/deployments/{dep_id}", dependencies=[Depends(require_key)], tags=["deployments"])
def get_deployment(dep_id: str):
    """One deployment with its full activity history and emails."""
    with db.session() as conn:
        dep = db.get_deployment(conn, dep_id)
        if not dep:
            raise HTTPException(404, f"Deployment {dep_id} not found")
        dep["activity"] = [dict(r) for r in conn.execute(
            "SELECT * FROM activity WHERE deployment_id = ? ORDER BY ts DESC, id DESC", (dep_id,))]
        dep["emails"] = [dict(r) for r in conn.execute(
            "SELECT * FROM outbox WHERE deployment_id = ? ORDER BY id DESC", (dep_id,))]
        return dep


@app.get("/api/summary", dependencies=[Depends(require_key)], tags=["info"])
def summary(phase: Optional[str] = None):
    """Counts by status, phase and tech, plus how many are waiting on users."""
    with db.session() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM deployments")]
    if phase not in (None, ""):
        rows = [r for r in rows if r["phase"] == phase]
    now = datetime.now(timezone.utc)
    waiting = [r for r in rows if r["status"] == "Needs Scheduling"]
    stale = [r["id"] for r in waiting
             if (workflow.parse_dt(r["scheduling_requested_at"]) or now) < now - timedelta(days=7)]
    replace_total = sum(1 for r in rows if not r["status"].startswith("No Replace"))
    done = sum(1 for r in rows if r["status"] in ("Deployed", "Complete"))
    return {
        "total": len(rows),
        "by_status": dict(Counter(r["status"] for r in rows)),
        "by_phase": dict(Counter(r["phase"] or "(none)" for r in rows)),
        "by_tech": dict(Counter((r["tech"] or "(unassigned)").strip() for r in rows)),
        "to_replace": replace_total,
        "deployed_or_complete": done,
        "percent_done": round(100 * done / replace_total, 1) if replace_total else 0,
        "waiting_on_user": len(waiting),
        "waiting_over_7_days": stale,
        "old_devices_outstanding": sum(1 for r in rows if r["status"] == "Deployed"
                                       and r["old_device_returned"].lower() not in ("yes", "n/a")),
    }


LIFECYCLE = [
    ("verifying", "Verifying", ["Verify", "Follow Up"]),
    ("ready", "Ready to send", ["Replace"]),
    ("waiting", "Waiting on user", ["Needs Scheduling"]),
    ("scheduled", "Scheduled", ["Scheduled"]),
    ("deployed", "Deployed", ["Deployed"]),
    ("complete", "Complete", ["Complete"]),
]


@app.get("/api/insights", dependencies=[Depends(require_key)], tags=["info"])
def insights(phase: Optional[str] = None):
    """Lifecycle stage counts, average days between stages, weekly throughput, phase progress."""
    with db.session() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM deployments")]
        acts = [dict(r) for r in conn.execute(
            "SELECT deployment_id, ts, kind, message FROM activity ORDER BY id")]
    if phase not in (None, ""):
        rows = [r for r in rows if r["phase"] == phase]
    ids = {r["id"] for r in rows}
    acts = [a for a in acts if a["deployment_id"] in ids]

    # first time each deployment entered each status
    entered = {}
    for a in acts:
        if a["kind"] == "status" and "→ " in a["message"]:
            st = a["message"].split("→ ", 1)[1].split(" (")[0].strip()
            entered.setdefault(a["deployment_id"], {}).setdefault(st, a["ts"])

    def avg_days(a, b):
        spans = []
        for e in entered.values():
            if a in e and b in e:
                d = (workflow.parse_dt(e[b]) - workflow.parse_dt(e[a])).total_seconds() / 86400
                if d >= 0:
                    spans.append(d)
        return round(sum(spans) / len(spans), 1) if spans else None

    now = datetime.now(timezone.utc)
    week0 = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    weeks = [week0 - timedelta(weeks=i) for i in range(7, -1, -1)]
    weekly = [0] * 8
    for e in entered.values():
        ts = workflow.parse_dt(e.get("Deployed", ""))
        if ts and ts <= now:
            for i in range(7, -1, -1):
                if ts >= weeks[i]:
                    weekly[i] += 1
                    break

    phases = {}
    for r in rows:
        if r["status"].startswith("No Replace"):
            continue
        p = phases.setdefault(r["phase"] or "—", {"done": 0, "in_progress": 0, "not_started": 0})
        if r["status"] in ("Deployed", "Complete"):
            p["done"] += 1
        elif r["status"] in ("Verify", "Follow Up"):
            p["not_started"] += 1
        else:
            p["in_progress"] += 1

    kinds = Counter(a["kind"] for a in acts)
    auto_emails = sum(1 for a in acts if a["kind"] == "email")
    waiting = [r for r in rows if r["status"] == "Needs Scheduling"]
    return {
        "stages": [{"key": k, "label": label, "statuses": sts,
                    "count": sum(1 for r in rows if r["status"] in sts)} for k, label, sts in LIFECYCLE],
        "no_replace": sum(1 for r in rows if r["status"].startswith("No Replace")),
        "avg_days": {
            "request_to_booked": avg_days("Needs Scheduling", "Scheduled"),
            "booked_to_deployed": avg_days("Scheduled", "Deployed"),
            "deployed_to_complete": avg_days("Deployed", "Complete"),
            "request_to_complete": avg_days("Needs Scheduling", "Complete"),
        },
        "weekly_deployed": [{"week": w.date().isoformat(), "count": c} for w, c in zip(weeks, weekly)],
        "phases": [{"phase": k, **v} for k, v in sorted(phases.items())],
        "waiting_escalated": sum(1 for r in waiting if r["escalated_at"]),
        "waiting_reminded": sum(1 for r in waiting if r["reminder_count"]),
        "automation": {"emails": auto_emails, "bookings_synced": kinds.get("booking", 0),
                       "tickets_queued": sum(1 for a in acts if a["kind"] == "ticket")},
    }


@app.get("/api/todo", dependencies=[Depends(require_key)], tags=["info"])
def todo():
    """Manual tasks for the team: tickets to create/close, unresponsive users, devices to collect."""
    with db.session() as conn:
        return workflow.todo(conn)


@app.get("/api/outbox", dependencies=[Depends(require_key)], tags=["info"])
def outbox(limit: int = 200):
    with db.session() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT ?",
                                              (limit,))]


@app.get("/api/activity", dependencies=[Depends(require_key)], tags=["info"])
def recent_activity(limit: int = 100):
    with db.session() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM activity ORDER BY ts DESC, id DESC LIMIT ?",
                                              (limit,))]


# ---------------------------------------------------------------- write endpoints

@app.post("/api/deployments/{dep_id}/status", dependencies=[Depends(require_key)],
          tags=["deployments"])
def change_status(dep_id: str, body: StatusChange):
    """Change status. 'Needs Scheduling' emails the user the Bookings link and queues the ticket;
    'Complete' queues closing the ticket."""
    try:
        with db.session() as conn:
            return workflow.set_status(conn, dep_id, body.status, body.actor, body.reason)
    except workflow.WorkflowError as exc:
        _err(exc)


@app.post("/api/deployments/bulk-status", dependencies=[Depends(require_key)], tags=["deployments"])
def bulk_status(body: BulkStatus):
    results = {"ok": [], "errors": {}}
    for dep_id in body.ids:
        try:
            with db.session() as conn:
                workflow.set_status(conn, dep_id, body.status, body.actor)
            results["ok"].append(dep_id)
        except workflow.WorkflowError as exc:
            results["errors"][dep_id] = str(exc)
    return results


@app.patch("/api/deployments/{dep_id}", dependencies=[Depends(require_key)], tags=["deployments"])
def patch_deployment(dep_id: str, body: DeploymentPatch):
    """Edit fields (REQ#, email, tech, old device returned, ...)."""
    fields = body.dict(exclude={"actor"})
    try:
        with db.session() as conn:
            return workflow.update_deployment(conn, dep_id, fields, body.actor)
    except workflow.WorkflowError as exc:
        _err(exc)


@app.post("/api/deployments/{dep_id}/notes", dependencies=[Depends(require_key)],
          tags=["deployments"])
def add_note(dep_id: str, body: NoteIn):
    try:
        with db.session() as conn:
            workflow.add_note(conn, dep_id, body.text, body.actor)
    except workflow.WorkflowError as exc:
        _err(exc)
    return {"ok": True}


@app.post("/api/deployments/{dep_id}/ticket-closed", dependencies=[Depends(require_key)],
          tags=["deployments"])
def ticket_closed(dep_id: str, actor: str = "user"):
    """Mark that the ticket was closed in the Service Dashboard."""
    with db.session() as conn:
        if not db.get_deployment(conn, dep_id):
            raise HTTPException(404, f"Deployment {dep_id} not found")
        db.update_fields(conn, dep_id, {"close_ticket_pending": 0})
        db.log(conn, dep_id, "ticket", "Ticket closed in Service Dashboard", actor)
    return {"ok": True}


@app.post("/api/reminders/run", dependencies=[Depends(require_key)], tags=["automation"])
def run_reminders(days_ahead: float = Query(0, description="Demo: pretend this many days passed")):
    """Send due reminders/escalations now (also runs automatically in the background)."""
    now = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    with db.session() as conn:
        return workflow.run_reminders(conn, now=now)


@app.post("/api/bookings/webhook", tags=["automation"])
def bookings_webhook(body: BookingEvent, x_webhook_secret: Optional[str] = Header(default=None),
                     x_api_key: Optional[str] = Header(default=None)):
    """Called by a Power Automate flow (Microsoft Bookings trigger) or the demo button.
    Matches the booking to a deployment by the customer's email."""
    allowed = (
        (not config.BOOKINGS_WEBHOOK_SECRET and not config.API_KEY)
        or (config.BOOKINGS_WEBHOOK_SECRET and x_webhook_secret == config.BOOKINGS_WEBHOOK_SECRET)
        or (config.API_KEY and x_api_key == config.API_KEY)
    )
    if not allowed:
        raise HTTPException(401, "Invalid X-Webhook-Secret / X-API-Key header")
    if body.event not in ("created", "updated", "cancelled"):
        raise HTTPException(400, "event must be created, updated or cancelled")
    try:
        with db.session() as conn:
            matched = workflow.handle_booking(conn, body.event, body.customer_email, body.start,
                                              body.end, body.deployment_id)
    except workflow.WorkflowError as exc:
        _err(exc)
    return {"matched": matched}


@app.post("/api/import", dependencies=[Depends(require_key)], tags=["data"])
async def import_tracker(file: UploadFile = File(...), overwrite: bool = False):
    """Import the Excel tracker (saved as CSV). Existing IDs are skipped unless overwrite=true."""
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    return importer.import_csv(text, overwrite=overwrite)


@app.get("/api/export.csv", dependencies=[Depends(require_key)], tags=["data"])
def export():
    return PlainTextResponse(importer.export_csv(), media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=deploytrack-export.csv"})


# ---------------------------------------------------------------- email + AI

class EmailIn(BaseModel):
    subject: str
    body: str
    actor: str = "user"


class AskIn(BaseModel):
    question: str
    history: List[dict] = []


class ReplyIn(BaseModel):
    text: str


class ApplyReply(BaseModel):
    reply_text: str = ""
    status: Optional[str] = None
    scheduled_start: Optional[str] = None
    note: str = ""
    reply_subject: str = ""
    reply_body: str = ""
    send_reply: bool = True
    actor: str = "user"


_ai_hits = {}      # ip -> [timestamps in the last hour]
_ai_day = {"date": None, "count": 0}


def ai_limit(request: Request):
    """Protect the AI bill on a public deployment. Offline rules are free and never limited."""
    if config.AI_PROVIDER == "offline":
        return
    now = datetime.now(timezone.utc)
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "?"))
    hits = [t for t in _ai_hits.get(ip, []) if (now - t).total_seconds() < 3600]
    if _ai_day["date"] != now.date():
        _ai_day.update(date=now.date(), count=0)
    if len(hits) >= config.AI_HOURLY_LIMIT_PER_IP:
        raise HTTPException(429, "You've hit this demo's AI limit for the hour. Try again later.")
    if _ai_day["count"] >= config.AI_DAILY_LIMIT:
        raise HTTPException(429, "This demo's AI limit for today has been reached. Try again tomorrow.")
    hits.append(now)
    _ai_hits[ip] = hits
    _ai_day["count"] += 1


def _ai_call(fn, *args):
    try:
        return fn(*args)
    except ai.AIError as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/deployments/{dep_id}/email", dependencies=[Depends(require_key)], tags=["deployments"])
def send_custom_email(dep_id: str, body: EmailIn):
    """Send a one-off email to the user (e.g. an approved AI-drafted follow-up)."""
    with db.session() as conn:
        dep = db.get_deployment(conn, dep_id)
        if not dep:
            raise HTTPException(404, f"Deployment {dep_id} not found")
        if not dep["user_email"]:
            raise HTTPException(400, "This deployment has no user email")
        send_email(conn, dep_id, dep["user_email"], body.subject, body.body, actor=body.actor)
        db.update_fields(conn, dep_id, {"last_contacted_at": db.now_iso()})
    return {"ok": True}


@app.get("/api/ai/status", dependencies=[Depends(require_key)], tags=["ai"])
def ai_status():
    return ai.status()


@app.post("/api/ai/ask", dependencies=[Depends(require_key), Depends(ai_limit)], tags=["ai"])
def ai_ask(body: AskIn):
    """Ask a plain-English question about the deployments."""
    with db.session() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM deployments ORDER BY id")]
    return _ai_call(ai.ask, body.question, rows, summary(), body.history)


@app.post("/api/ai/followup/{dep_id}", dependencies=[Depends(require_key), Depends(ai_limit)], tags=["ai"])
def ai_followup(dep_id: str):
    """Draft a personal follow-up for someone who hasn't booked. Nothing is sent."""
    dep = get_deployment(dep_id)
    return _ai_call(ai.draft_followup, dep, dep["activity"])


@app.post("/api/ai/reply/{dep_id}", dependencies=[Depends(require_key), Depends(ai_limit)], tags=["ai"])
def ai_interpret_reply(dep_id: str, body: ReplyIn):
    """Read a user's email reply and suggest a status, note and response. Nothing is changed."""
    dep = get_deployment(dep_id)
    return _ai_call(ai.interpret_reply, dep, body.text)


@app.post("/api/ai/reply/{dep_id}/apply", dependencies=[Depends(require_key)], tags=["ai"])
def ai_apply_reply(dep_id: str, body: ApplyReply):
    """Apply an (edited/approved) suggestion from /api/ai/reply."""
    try:
        with db.session() as conn:
            dep = db.get_deployment(conn, dep_id)
            if not dep:
                raise HTTPException(404, f"Deployment {dep_id} not found")
            if body.reply_text:
                db.log(conn, dep_id, "note", f"User replied: \"{body.reply_text.strip()[:500]}\"", body.actor)
            if body.note:
                db.log(conn, dep_id, "note", body.note, body.actor)
            if body.status == "Scheduled" and body.scheduled_start:
                db.update_fields(conn, dep_id, {"scheduled_start": body.scheduled_start,
                                                "scheduled_end": ""})
            if body.status and body.status != dep["status"]:
                workflow.set_status(conn, dep_id, body.status, body.actor, "from user's reply",
                                    notify=not (body.send_reply and body.reply_body))
            if body.send_reply and body.reply_body and dep["user_email"]:
                send_email(conn, dep_id, dep["user_email"], body.reply_subject or "Re: your new computer",
                           body.reply_body, actor=body.actor)
                db.update_fields(conn, dep_id, {"last_contacted_at": db.now_iso()})
            return db.get_deployment(conn, dep_id)
    except workflow.WorkflowError as exc:
        _err(exc)


@app.post("/api/demo/reset", dependencies=[Depends(require_key)], tags=["data"])
def demo_reset():
    """Wipe and regenerate the fake demo data. Only works on the demo database."""
    if not config.DEMO_DB:
        raise HTTPException(403, "Reset is only allowed on the demo database")
    return demo_data.reset()


@app.post("/api/demo/play-record", dependencies=[Depends(require_key)], tags=["data"])
def demo_play_record():
    """Add a fresh fictional 'ready to send' person for the Play walkthrough (demo database only)."""
    if not config.DEMO_DB:
        raise HTTPException(403, "Only available on the demo database")
    with db.session() as conn:
        return {"id": demo_data.new_ready_record(conn)}


# ---------------------------------------------------------------- UI

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
