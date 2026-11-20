"""Deployment status workflow and the automations attached to each status."""
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover
    LOCAL_TZ = timezone.utc

from . import config, db
from .integrations import request_ticket, request_ticket_close, send_email

PIPELINE = ["Verify", "Follow Up", "Replace", "Needs Scheduling", "Scheduled", "Deployed", "Complete"]
NO_REPLACE = [
    "No Replace - Action Needed", "No Replace - Duplicate", "No Replace - Not UWF",
    "No Replace - Operational", "No Replace - Resigned", "No Replace - Retired",
    "No Replace - Surplus",
]
STATUSES = PIPELINE + NO_REPLACE


class WorkflowError(ValueError):
    pass


def parse_dt(value: str):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt


def fmt_dt(value: str) -> str:
    dt = parse_dt(value)
    if not dt:
        return value or ""
    dt = dt.astimezone(LOCAL_TZ)
    hour = dt.hour % 12 or 12
    return f"{dt:%A, %B} {dt.day} at {hour}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


# ---------------------------------------------------------------- email templates

def _first_name(dep):
    return (dep["user_name"] or dep["netid"] or "there").split()[0]


def _ref(dep):
    return f"{dep['id']}" + (f" / {dep['req_number']}" if dep["req_number"] else "")


def email_schedule_request(dep):
    return (
        f"Your new computer is ready — please schedule your setup ({_ref(dep)})",
        f"Hi {_first_name(dep)},\n\n"
        f"As part of the 2026 UWF Computer Devices Refresh, your new "
        f"{(dep['deployment_type'] or 'device').lower()} is ready for you.\n\n"
        f"Please book a setup appointment with us here:\n{config.BOOKINGS_URL}\n\n"
        f"The appointment takes about an hour. Please bring your current device"
        f"{' and accessories' if dep['accessories'] else ''} so we can transfer your setup "
        f"and collect the old one.\n\n"
        f"Questions? Just reply to this email.\n\nThanks,\n{config.TEAM_NAME}",
    )


def email_reminder(dep, n):
    return (
        f"Reminder: please schedule your new computer setup ({_ref(dep)})",
        f"Hi {_first_name(dep)},\n\n"
        f"Just a friendly reminder (#{n}) that your new computer is ready and waiting. "
        f"Please pick a time that works for you:\n{config.BOOKINGS_URL}\n\n"
        f"If you have any questions or none of the times work, reply to this email and we "
        f"will find a time.\n\nThanks,\n{config.TEAM_NAME}",
    )


def email_escalation(dep):
    return (
        f"Help needed: {dep['user_name'] or dep['netid']} has not scheduled their computer refresh",
        f"Hi {(dep['dept_contact'] or 'there').strip()},\n\n"
        f"We have contacted {dep['user_name'] or dep['netid']} ({dep['department']}) "
        f"{dep['reminder_count'] + 1} times about scheduling the setup for their new computer, "
        f"but haven't received a booking yet.\n\n"
        f"Could you help us connect with them? They can book here:\n{config.BOOKINGS_URL}\n\n"
        f"Reference: {_ref(dep)}\n\nThanks,\n{config.TEAM_NAME}",
    )


def email_confirmation(dep):
    return (
        f"Confirmed: computer setup on {fmt_dt(dep['scheduled_start'])} ({_ref(dep)})",
        f"Hi {_first_name(dep)},\n\n"
        f"Thanks for booking! We'll see you on {fmt_dt(dep['scheduled_start'])}"
        f"{' at ' + dep['location'] if dep['location'] else ''}.\n\n"
        f"Please bring your current device"
        f"{' and accessories' if dep['accessories'] else ''}.\n\nThanks,\n{config.TEAM_NAME}",
    )


def email_rebook(dep):
    return (
        f"Your computer setup was cancelled — please rebook ({_ref(dep)})",
        f"Hi {_first_name(dep)},\n\n"
        f"Your setup appointment was cancelled. When you're ready, please choose a new time:\n"
        f"{config.BOOKINGS_URL}\n\nThanks,\n{config.TEAM_NAME}",
    )


# ---------------------------------------------------------------- transitions

def set_status(conn, dep_id, new_status, actor="system", reason="", notify=True):
    if new_status not in STATUSES:
        raise WorkflowError(f"Unknown status '{new_status}'. Valid: {', '.join(STATUSES)}")
    dep = db.get_deployment(conn, dep_id)
    if not dep:
        raise WorkflowError(f"Deployment {dep_id} not found")
    old = dep["status"]
    if old == new_status:
        return dep

    if new_status == "Needs Scheduling" and not dep["user_email"]:
        raise WorkflowError("Add the user's email before moving to Needs Scheduling.")

    db.update_fields(conn, dep_id, {"status": new_status})
    db.log(conn, dep_id, "status", f"{old or '(none)'} → {new_status}"
           + (f" ({reason})" if reason else ""), actor)
    dep = db.get_deployment(conn, dep_id)

    if new_status == "Needs Scheduling":
        request_ticket(conn, dep, actor)
        if old == "Scheduled":
            subject, body = email_rebook(dep)
        else:
            subject, body = email_schedule_request(dep)
        send_email(conn, dep_id, dep["user_email"], subject, body, actor=actor)
        now = db.now_iso()
        db.update_fields(conn, dep_id, {
            "scheduling_requested_at": dep["scheduling_requested_at"] or now,
            "last_contacted_at": now, "reminder_count": 0, "escalated_at": "",
            "scheduled_start": "", "scheduled_end": "",
        })

    elif new_status == "Scheduled" and notify and dep["scheduled_start"] and dep["user_email"]:
        subject, body = email_confirmation(dep)
        send_email(conn, dep_id, dep["user_email"], subject, body, actor=actor)

    elif new_status == "Deployed" and dep["old_device_returned"].lower() == "yes":
        return set_status(conn, dep_id, "Complete", actor, "old device already returned")

    elif new_status == "Complete":
        request_ticket_close(conn, dep, actor)

    return db.get_deployment(conn, dep_id)


def update_deployment(conn, dep_id, fields: dict, actor="user"):
    dep = db.get_deployment(conn, dep_id)
    if not dep:
        raise WorkflowError(f"Deployment {dep_id} not found")
    changes = {k: v for k, v in fields.items() if k in db.EDITABLE_FIELDS and v is not None
               and str(v) != str(dep[k])}
    if "req_number" in changes and changes["req_number"]:
        changes["ticket_pending"] = 0
    db.update_fields(conn, dep_id, changes)
    for k, v in changes.items():
        if k in ("ticket_pending",):
            continue
        db.log(conn, dep_id, "edit", f"{k.replace('_', ' ')}: '{dep[k]}' → '{v}'", actor)

    dep = db.get_deployment(conn, dep_id)
    if ("old_device_returned" in changes and dep["old_device_returned"].lower() == "yes"
            and dep["status"] == "Deployed"):
        dep = set_status(conn, dep_id, "Complete", actor, "old device returned")
    return dep


def add_note(conn, dep_id, text, actor="user"):
    if not db.get_deployment(conn, dep_id):
        raise WorkflowError(f"Deployment {dep_id} not found")
    db.log(conn, dep_id, "note", text, actor)


# ---------------------------------------------------------------- bookings

def handle_booking(conn, event, customer_email="", start="", end="", deployment_id="",
                   actor="bookings"):
    """event: 'created' | 'updated' | 'cancelled'. Returns the IDs that were updated."""
    if deployment_id:
        rows = [db.get_deployment(conn, deployment_id)]
        rows = [r for r in rows if r]
    else:
        email = (customer_email or "").strip().lower()
        if not email:
            raise WorkflowError("Booking needs customer_email or deployment_id")
        local = email.split("@")[0]
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM deployments WHERE (lower(user_email) = ? OR lower(netid) = ?)"
            " AND status IN ('Needs Scheduling', 'Scheduled')", (email, local))]
    if not rows:
        return []

    updated = []
    for dep in rows:
        if event in ("created", "updated"):
            db.update_fields(conn, dep["id"], {"scheduled_start": start, "scheduled_end": end})
            db.log(conn, dep["id"], "booking", f"Booked for {fmt_dt(start)}", actor)
            if dep["status"] == "Scheduled":
                db.log(conn, dep["id"], "booking", "Rescheduled", actor)
                subject, body = email_confirmation(db.get_deployment(conn, dep["id"]))
                send_email(conn, dep["id"], dep["user_email"], subject, body, actor=actor)
            else:
                set_status(conn, dep["id"], "Scheduled", actor, "booked in Bookings")
        elif event == "cancelled":
            db.log(conn, dep["id"], "booking", "Booking cancelled", actor)
            if dep["status"] == "Scheduled":
                set_status(conn, dep["id"], "Needs Scheduling", actor, "booking cancelled")
        updated.append(dep["id"])
    return updated


# ---------------------------------------------------------------- reminders

def run_reminders(conn, now=None, actor="reminder-bot"):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=config.REMINDER_INTERVAL_DAYS)
    results = {"reminded": [], "escalated": [], "needs_contact_email": []}
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM deployments WHERE status = 'Needs Scheduling' AND escalated_at = ''")]
    for dep in rows:
        last = parse_dt(dep["last_contacted_at"]) or parse_dt(dep["scheduling_requested_at"])
        if last and last > cutoff:
            continue
        stamp = now.replace(microsecond=0).isoformat()
        if dep["reminder_count"] < config.MAX_REMINDERS:
            n = dep["reminder_count"] + 1
            subject, body = email_reminder(dep, n)
            send_email(conn, dep["id"], dep["user_email"], subject, body, actor=actor)
            db.update_fields(conn, dep["id"], {"reminder_count": n, "last_contacted_at": stamp})
            results["reminded"].append(dep["id"])
        elif dep["dept_contact_email"]:
            subject, body = email_escalation(dep)
            send_email(conn, dep["id"], dep["dept_contact_email"], subject, body,
                       cc=dep["user_email"], actor=actor)
            db.update_fields(conn, dep["id"], {"escalated_at": stamp, "last_contacted_at": stamp})
            results["escalated"].append(dep["id"])
        else:
            db.log(conn, dep["id"], "reminder",
                   "No response after reminders and no department contact email — follow up "
                   "manually.", actor)
            db.update_fields(conn, dep["id"], {"escalated_at": stamp})
            results["needs_contact_email"].append(dep["id"])
    return results


# ---------------------------------------------------------------- views

def todo(conn):
    """Everything a person on the team needs to do by hand, in one list."""
    items = []

    def rows(sql, *args):
        return [dict(r) for r in conn.execute(sql, args)]

    for d in rows("SELECT * FROM deployments WHERE ticket_pending = 1 ORDER BY id"):
        from .integrations import ticket_text
        items.append({"kind": "create_ticket", "deployment_id": d["id"],
                      "title": f"Create Service Dashboard ticket for {d['user_name'] or d['netid']}",
                      "ticket": ticket_text(d)})
    for d in rows("SELECT * FROM deployments WHERE close_ticket_pending = 1 ORDER BY id"):
        items.append({"kind": "close_ticket", "deployment_id": d["id"],
                      "title": f"Close {d['req_number']} ({d['user_name']})"})
    for d in rows("SELECT * FROM deployments WHERE status = 'Needs Scheduling' AND escalated_at != ''"
                  " ORDER BY id"):
        items.append({"kind": "unresponsive", "deployment_id": d["id"],
                      "title": f"{d['user_name'] or d['netid']} hasn't booked after reminders"
                               + ("" if d["dept_contact_email"] else " (no dept contact email)")})
    for d in rows("SELECT * FROM deployments WHERE status = 'Deployed' AND"
                  " lower(old_device_returned) != 'yes' AND lower(old_device_returned) != 'n/a'"
                  " ORDER BY id"):
        items.append({"kind": "collect_device", "deployment_id": d["id"],
                      "title": f"Collect old device from {d['user_name'] or d['netid']}"})
    for d in rows("SELECT * FROM deployments WHERE status IN ('Needs Scheduling','Replace')"
                  " AND user_email = '' ORDER BY id"):
        items.append({"kind": "missing_email", "deployment_id": d["id"],
                      "title": f"Add email for {d['user_name'] or d['id']}"})
    today = datetime.now(LOCAL_TZ).date()
    for d in rows("SELECT * FROM deployments WHERE status = 'Scheduled' AND scheduled_start != ''"):
        dt = parse_dt(d["scheduled_start"])
        if dt and dt.astimezone(LOCAL_TZ).date() <= today + timedelta(days=1):
            items.append({"kind": "upcoming", "deployment_id": d["id"],
                          "title": f"Deployment {fmt_dt(d['scheduled_start'])}: "
                                   f"{d['user_name']} ({d['tech'] or 'no tech assigned'})"})
    return items
