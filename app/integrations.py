"""Outbound integrations: email and ServiceNow.

Email is always recorded in the outbox table. It is only actually delivered when
EMAIL_MODE=smtp. ServiceNow has no API access, so "manual" mode creates a to-do with
prefilled ticket text, and "email" mode sends the request to an intake mailbox.
"""
import smtplib
from email.message import EmailMessage

from . import config, db


def send_email(conn, deployment_id, to_addr, subject, body, cc="", actor="system"):
    real_to, real_cc = to_addr, cc
    if config.REDIRECT_ALL_EMAIL_TO:
        subject = f"[TEST -> {to_addr}] {subject}"
        real_to, real_cc = config.REDIRECT_ALL_EMAIL_TO, ""

    delivered, error = 0, ""
    if config.EMAIL_MODE == "smtp":
        try:
            msg = EmailMessage()
            msg["From"] = config.SMTP_USER or config.TEAM_EMAIL
            msg["To"] = real_to
            if real_cc:
                msg["Cc"] = real_cc
            msg["Reply-To"] = config.TEAM_EMAIL
            msg["Subject"] = subject
            msg.set_content(body)
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
                s.starttls()
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
            delivered = 1
        except Exception as exc:  # keep the workflow going; surface the error in the UI
            error = str(exc)

    conn.execute(
        "INSERT INTO outbox (ts, deployment_id, to_addr, cc, subject, body, delivered, error)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (db.now_iso(), deployment_id, to_addr, cc, subject, body, delivered, error),
    )
    where = "sent" if delivered else ("FAILED: " + error if error else "saved to outbox")
    db.log(conn, deployment_id, "email", f"Email to {to_addr}{' (cc ' + cc + ')' if cc else ''}: "
           f"\"{subject}\" — {where}", actor)


def ticket_text(dep) -> dict:
    return {
        "short_description": f"Device refresh deployment {dep['id']} — {dep['user_name'] or dep['netid']}",
        "description": (
            f"Computer refresh deployment for {dep['user_name']} ({dep['netid']}).\n"
            f"Department: {dep['department']}\n"
            f"Deployment type: {dep['deployment_type']}\n"
            f"New device SN: {dep['new_device_sn'] or 'TBD'}\n"
            f"Old device SN: {dep['old_device_sn'] or 'TBD'}\n"
            f"Accessories: {dep['accessories']}\n"
            f"Location: {dep['location']}\n"
            f"Tracker ID: {dep['id']}"
        ),
        "assigned_to": dep["tech"],
        "requested_for": dep["netid"],
    }


def request_ticket(conn, dep, actor="system"):
    if dep["req_number"]:
        return
    t = ticket_text(dep)
    if config.SNOW_MODE == "email":
        send_email(conn, dep["id"], config.SNOW_INBOUND_EMAIL, t["short_description"],
                   t["description"], cc=dep["user_email"], actor=actor)
        db.log(conn, dep["id"], "ticket",
               "Ticket requested by email; paste the REQ# here once it arrives.", actor)
    else:
        db.log(conn, dep["id"], "ticket",
               "Ticket needed: create it in the Service Dashboard (text prefilled under To-Do), "
               "then paste the REQ# here.", actor)
    db.update_fields(conn, dep["id"], {"ticket_pending": 1})


def request_ticket_close(conn, dep, actor="system"):
    if not dep["req_number"]:
        return
    if config.SNOW_MODE == "email":
        send_email(conn, dep["id"], config.SNOW_INBOUND_EMAIL,
                   f"{dep['req_number']} — deployment complete, please close",
                   f"Deployment {dep['id']} for {dep['user_name']} is complete and the old "
                   f"device has been returned.", actor=actor)
    db.log(conn, dep["id"], "ticket", f"Close {dep['req_number']} in the Service Dashboard.", actor)
    db.update_fields(conn, dep["id"], {"close_ticket_pending": 1})
