"""Realistic fake dataset for presentations. No real people, emails or ticket numbers.

Dates are generated relative to *now*, so 'today' and 'tomorrow' always have appointments
no matter when the demo is run. All emails use the reserved example domain.
"""
import random
from datetime import datetime, timedelta, timezone

from . import config, db, workflow

FIRST = ["Maria", "James", "Priya", "Daniel", "Aisha", "Kevin", "Sofia", "Marcus", "Elena", "Tom",
         "Grace", "Omar", "Hannah", "Luis", "Mei", "Robert", "Fatima", "Nathan", "Chloe", "Victor",
         "Ingrid", "Samuel", "Leah", "Andre", "Yuki", "Brian", "Nadia", "Carlos", "Erin", "Ravi",
         "Olivia", "Hassan", "Megan", "Diego", "Julia", "Kofi", "Rachel", "Theo", "Lena", "Isaac"]
LAST = ["Lopez", "Nguyen", "Patel", "Kim", "Johnson", "Okafor", "Garcia", "Chen", "Rossi", "Walsh",
        "Haddad", "Larsen", "Brooks", "Silva", "Tanaka", "Murphy", "Ahmed", "Fischer", "Reyes", "Cole",
        "Novak", "Grant", "Bauer", "Mensah", "Ito", "Price", "Kowalski", "Ortiz", "Hayes", "Sharma"]
DEPTS = [("Project Delivery Group", "Dana Whitfield"), ("Architecture", "Jordan Ellis"),
         ("Building Environmental Controls", "Casey Morgan"), ("Finance and Administration", "Riley Chen"),
         ("Business Intelligence", "Alex Romero"), ("Facilities Maintenance", "Sam Patel"),
         ("Grounds & Landscaping", "Taylor Brooks"), ("Custodial Services", "Morgan Lee"),
         ("Capital Projects", "Jamie Ortiz"), ("Transportation Services", "Avery Kim")]
TECHS = ["Alex", "Bea", "Chris", "Dev", "Jo", "Kai"]
NOTES = ["Prefers mornings", "Hybrid — on site Tue/Thu", "Needs a 2nd monitor cable",
         "Wants to keep current dock", "Uses specialized CAD software — confirm license transfer",
         "Shares office with two others", "Remote most days, can come to T13", ""]
DOMAIN = "example.edu"


def _iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _business_slot(day: datetime, rng) -> datetime:
    local = day.astimezone(workflow.LOCAL_TZ)
    while local.weekday() >= 5:
        local += timedelta(days=1)
    return local.replace(hour=rng.choice([9, 10, 11, 13, 14, 15]), minute=rng.choice([0, 30]),
                         second=0, microsecond=0)


def _plan():
    """(phase, status, count) — mirrors the real tracker's shape."""
    return [
        ("0", "No Replace - Duplicate", 12), ("0", "No Replace - Surplus", 9), ("0", "No Replace - Retired", 6),
        ("0", "No Replace - Operational", 6), ("0", "Complete", 7),
        ("1", "Complete", 38), ("1", "Deployed", 14), ("1", "No Replace - Resigned", 2), ("1", "Follow Up", 1),
        ("2", "Verify", 38), ("2", "Follow Up", 7), ("2", "Replace", 14), ("2", "Needs Scheduling", 26),
        ("2", "Scheduled", 13), ("2", "Deployed", 9), ("2", "Complete", 9), ("2", "No Replace - Action Needed", 2),
        ("3", "Verify", 22),
    ]


def seed(conn, rng_seed: int = 7):
    rng = random.Random(rng_seed)
    now = datetime.now(timezone.utc)
    conn.execute("DELETE FROM deployments")
    conn.execute("DELETE FROM activity")
    conn.execute("DELETE FROM outbox")

    used_names, n, req = set(), 100, 9100000
    rows = []
    for phase, status, count in _plan():
        for _ in range(count):
            while True:
                name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                if name not in used_names:
                    used_names.add(name)
                    break
            first, last = name.split()
            netid = (first[0] + last).lower()[:8] + str(rng.randint(1, 9))
            dept, contact = rng.choice(DEPTS)
            laptop = rng.random() < 0.62
            shared = rng.random() < 0.12 and (status.startswith("No Replace") or status in ("Verify", "Follow Up"))
            n += 1
            d = {
                "id": f"D{n:03d}", "phase": phase, "status": status,
                "deployment_type": "Standard Laptop" if laptop else "Standard Desktop",
                "accessories": "Dock, Keyboard/Mouse, Video Cables" if laptop else "Keyboard/Mouse",
                "location": rng.choice(["T13 Hybrid", "T13", "FPB", "Plant Services"]),
                "shared_device": "Yes" if shared else "No",
                "user_name": "" if shared else name, "netid": "" if shared else netid,
                "user_email": "" if shared else f"{first}.{last}@{DOMAIN}".lower(),
                "department": dept, "dept_contact": contact,
                "dept_contact_email": f"{contact.split()[0]}.{contact.split()[1]}@{DOMAIN}".lower(),
                "old_device_sn": "".join(rng.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(10)),
                "last_login": netid, "notes": rng.choice(NOTES), "dubsupport_tag": "",
                "tech": "", "req_number": "", "new_device_sn": "", "old_device_returned": "",
                "scheduled_start": "", "scheduled_end": "", "scheduling_requested_at": "",
                "last_contacted_at": "", "reminder_count": 0, "escalated_at": "",
                "ticket_pending": 0, "close_ticket_pending": 0,
            }
            if status.startswith("No Replace") or status in ("Verify", "Follow Up"):
                if status == "Follow Up":
                    d["notes"] = rng.choice(["Confirm user is still in department", "Waiting on supervisor approval",
                                             "Device not seen in DubSupport for 60+ days"])
            else:
                d["tech"] = rng.choice(TECHS)
                d["new_device_sn"] = "".join(rng.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(7))
                d["dubsupport_tag"] = "TRUE"
            rows.append(d)

    def add(d, kind, msg, when, actor="system"):
        if when > now:  # history can't be in the future
            when = now - timedelta(minutes=rng.randint(20, 300))
        conn.execute("INSERT INTO activity (deployment_id, ts, kind, actor, message) VALUES (?,?,?,?,?)",
                     (d["id"], _iso(when), kind, actor, msg))

    def mail(d, to, subject, body, when, cc=""):
        if when > now:
            when = now - timedelta(minutes=rng.randint(20, 300))
        conn.execute("INSERT INTO outbox (ts, deployment_id, to_addr, cc, subject, body, delivered, error)"
                     " VALUES (?,?,?,?,?,?,0,'')", (_iso(when), d["id"], to, cc, subject, body))
        add(d, "email", f"Email to {to}: \"{subject}\" — saved to outbox", when)

    sched_i = 0
    for d in rows:
        st = d["status"]
        created = now - timedelta(days=rng.randint(40, 70))
        add(d, "system", "Imported from tracker CSV", created)
        if st in ("Replace",):
            d["tech"] = d["tech"] or rng.choice(TECHS)
        if st in ("Needs Scheduling", "Scheduled", "Deployed", "Complete"):
            asked = now - timedelta(days=rng.randint(1, 15) if st == "Needs Scheduling"
                                    else rng.randint(9, 16) if st == "Scheduled" else rng.randint(8, 56))
            d["scheduling_requested_at"] = d["last_contacted_at"] = _iso(asked)
            has_ticket = not (st == "Needs Scheduling" and rng.random() < 0.3)
            if has_ticket:
                req += rng.randint(3, 400)
                d["req_number"] = f"REQ0{req}"
            add(d, "status", "Replace → Needs Scheduling", asked, d["tech"] or "web")
            if d["user_email"]:
                subj, body = workflow.email_schedule_request(d)
                mail(d, d["user_email"], subj, body, asked)
            if has_ticket:
                add(d, "edit", f"req number: '' → '{d['req_number']}'", asked + timedelta(hours=2), d["tech"])
            else:
                d["ticket_pending"] = 1
                add(d, "ticket", "Ticket needed: create it in the Service Dashboard, then paste the REQ# here.", asked)

            if st == "Needs Scheduling":
                age = (now - asked).days
                reminders = min(config.MAX_REMINDERS, int(age // config.REMINDER_INTERVAL_DAYS))
                last = asked
                for i in range(1, reminders + 1):
                    last = asked + timedelta(days=config.REMINDER_INTERVAL_DAYS * i)
                    subj, body = workflow.email_reminder(d, i)
                    mail(d, d["user_email"], subj, body, last)
                d["reminder_count"], d["last_contacted_at"] = reminders, _iso(last)
                if reminders >= config.MAX_REMINDERS and age > config.REMINDER_INTERVAL_DAYS * (reminders + 1):
                    esc_at = asked + timedelta(days=config.REMINDER_INTERVAL_DAYS * (reminders + 1))
                    subj, body = workflow.email_escalation(d)
                    mail(d, d["dept_contact_email"], subj, body, esc_at, cc=d["user_email"])
                    d["escalated_at"] = d["last_contacted_at"] = _iso(esc_at)
                continue

            booked = asked + timedelta(days=rng.randint(1, 4))
            if st == "Scheduled":
                offset = [0, 0, 1, 1, 2, 3, 3, 4, 5, 6, 7, 8, 9][sched_i % 13]
                sched_i += 1
                start = _business_slot(now + timedelta(days=offset), rng)
            else:
                start = _business_slot(booked + timedelta(days=rng.randint(2, 8)), rng)
                if start > now - timedelta(hours=3):  # deployments must be in the past
                    start = _business_slot(now - timedelta(days=rng.randint(1, 3)), rng)
                    while start > now - timedelta(hours=3):
                        start -= timedelta(days=1)
            d["scheduled_start"], d["scheduled_end"] = _iso(start), _iso(start + timedelta(hours=1))
            add(d, "booking", f"Booked for {workflow.fmt_dt(d['scheduled_start'])}", booked, "bookings")
            add(d, "status", "Needs Scheduling → Scheduled (booked in Bookings)", booked, "bookings")
            if d["user_email"]:
                subj, body = workflow.email_confirmation(d)
                mail(d, d["user_email"], subj, body, booked)
            if st in ("Deployed", "Complete"):
                add(d, "status", "Scheduled → Deployed", start + timedelta(hours=1), d["tech"])
                returned = st == "Complete"
                d["old_device_returned"] = "Yes" if returned else "No"
                if returned:
                    add(d, "edit", "old device returned: 'No' → 'Yes'", start + timedelta(days=rng.randint(0, 5)), d["tech"])
                    add(d, "status", "Deployed → Complete (old device returned)", start + timedelta(days=1), d["tech"])
                    add(d, "ticket", f"Ticket {d['req_number']} closed in Service Dashboard", start + timedelta(days=1), d["tech"])

    # A few pending ticket closes so the To-Do tab tells the whole story.
    for d in [r for r in rows if r["status"] == "Complete"][:3]:
        d["close_ticket_pending"] = 1

    # Showcase record for the AI "interpret reply" demo: has ignored two reminders.
    star = next(r for r in rows if r["status"] == "Needs Scheduling" and r["user_email"]
                and r["reminder_count"] >= 2 and not r["escalated_at"])
    star["notes"] = "Hybrid — on site Tue/Thu"

    for d in rows:
        d["created_at"] = d["updated_at"] = _iso(now)
        cols = ", ".join(d)
        conn.execute(f"INSERT INTO deployments ({cols}) VALUES ({', '.join('?' * len(d))})", tuple(d.values()))
    return {"deployments": len(rows), "showcase": star["id"], "showcase_name": star["user_name"]}


def new_ready_record(conn):
    """Add one fictional person in 'Replace' (ready to send), for the Play walkthrough."""
    rng = random.Random()
    taken = {r[0] for r in conn.execute("SELECT user_name FROM deployments")}
    for _ in range(200):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name not in taken:
            break
    first, last = name.split()
    n = 1 + max([int(r[0][1:]) for r in conn.execute("SELECT id FROM deployments WHERE id LIKE 'D%'")
                 if r[0][1:].isdigit()] or [100])
    dept, contact = rng.choice(DEPTS)
    now = _iso(datetime.now(timezone.utc))
    d = {
        "id": f"D{n:03d}", "phase": "2", "status": "Replace", "deployment_type": "Standard Laptop",
        "accessories": "Dock, Keyboard/Mouse, Video Cables", "location": "T13 Hybrid", "shared_device": "No",
        "user_name": name, "netid": (first[0] + last).lower()[:8] + str(rng.randint(1, 9)),
        "user_email": f"{first}.{last}@{DOMAIN}".lower(), "department": dept, "dept_contact": contact,
        "dept_contact_email": f"{contact.split()[0]}.{contact.split()[1]}@{DOMAIN}".lower(),
        "old_device_sn": "".join(rng.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(10)),
        "new_device_sn": "".join(rng.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(7)),
        "tech": rng.choice(TECHS), "notes": "Hybrid — on site Tue/Thu", "dubsupport_tag": "TRUE",
        "created_at": now, "updated_at": now,
    }
    d["last_login"] = d["netid"]
    conn.execute(f"INSERT INTO deployments ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})", tuple(d.values()))
    db.log(conn, d["id"], "system", "Verified in DubSupport — ready to send", "demo")
    return d["id"]


def reset():
    with db.session() as conn:
        return seed(conn)
