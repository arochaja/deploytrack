"""AI assistant features, backed by Purple (NebulaOne), Claude, or an offline rules engine.

Three features:
  ask(question)              plain-English questions about the deployments
  draft_followup(dep)        a personal nudge for someone ignoring reminders
  interpret_reply(dep, text) read a user's email reply and suggest what to do

DeployTrack calls the AI service (outbound HTTPS), so it works from a laptop: the AI never
needs to reach DeployTrack. Only the fields needed to answer are sent (no serial numbers).
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

from . import config, workflow


class AIError(RuntimeError):
    pass


def status():
    p = config.AI_PROVIDER
    live = p != "offline"
    if p == "claude":
        ready = bool(config.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY"))
        model = config.CLAUDE_MODEL
    else:
        ready = bool(config.AI_BASE_URL and config.AI_TOKEN) if live else True
        model = config.AI_MODEL
    return {"provider": p, "live": live, "ready": ready, "name": config.AI_NAME, "model": model}


def _complete(system: str, turns: list, schema: dict = None):
    """One call to whichever AI is configured. Returns text, or a dict when a schema is given."""
    if config.AI_PROVIDER == "claude":
        return _claude(system, turns, schema)
    text = _chat([{"role": "system", "content": system}] + turns, json_mode=bool(schema))
    return _parse_json(text) if schema else text


# ------------------------------------------------------------------ Claude provider

_claude_client = None


def _claude(system: str, turns: list, schema: dict = None):
    import anthropic

    global _claude_client
    if _claude_client is None:
        if not (config.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")):
            raise AIError("Claude is not configured: set ANTHROPIC_API_KEY in .env")
        _claude_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None,
                                             timeout=config.AI_TIMEOUT)
    output_config = {"effort": config.CLAUDE_EFFORT}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    try:
        resp = _claude_client.beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=16000,
            system=system,
            messages=turns,
            output_config=output_config,
            # If the model declines, the API retries on a fallback model in the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise AIError("Claude rejected the API key (check ANTHROPIC_API_KEY)")
    except anthropic.PermissionDeniedError as exc:
        raise AIError(f"Claude API key lacks permission: {exc.message}")
    except anthropic.RateLimitError:
        raise AIError("Claude rate limit reached; try again in a minute")
    except anthropic.APIStatusError as exc:
        raise AIError(f"Claude returned {exc.status_code}: {exc.message}")
    except anthropic.APIConnectionError:
        raise AIError("Could not reach the Claude API (network?)")
    if resp.stop_reason == "refusal":
        raise AIError("Claude declined this request")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return json.loads(text) if schema else text


# ------------------------------------------------------------------ live provider

def _chat(messages, json_mode=False) -> str:
    if not (config.AI_BASE_URL and config.AI_TOKEN):
        raise AIError("AI is not configured: set AI_BASE_URL and AI_TOKEN in .env")
    payload = {"messages": messages, "temperature": 0.2}
    if config.AI_MODEL:
        payload["model"] = config.AI_MODEL
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    prefix = config.AI_AUTH_PREFIX.strip()
    headers = {config.AI_AUTH_HEADER: f"{prefix} {config.AI_TOKEN}" if prefix else config.AI_TOKEN,
               "Content-Type": "application/json"}
    url = config.AI_BASE_URL + config.AI_CHAT_PATH
    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=config.AI_TIMEOUT)
        if r.status_code == 400 and json_mode:  # some gateways reject response_format
            payload.pop("response_format")
            r = httpx.post(url, json=payload, headers=headers, timeout=config.AI_TIMEOUT)
    except httpx.HTTPError as exc:
        raise AIError(f"Could not reach {config.AI_NAME}: {exc}") from exc
    if r.status_code >= 400:
        raise AIError(f"{config.AI_NAME} returned {r.status_code}: {r.text[:300]}")
    try:
        data = r.json()
    except ValueError:
        return r.text
    return _extract_text(data)


def _extract_text(data) -> str:
    """Accept the common response shapes (OpenAI-style and a few simpler ones)."""
    if isinstance(data, str):
        return data
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        pass
    for key in ("output_text", "response", "content", "answer", "text", "message"):
        v = data.get(key) if isinstance(data, dict) else None
        if isinstance(v, str):
            return v
        if isinstance(v, dict) and isinstance(v.get("content"), str):
            return v["content"]
    raise AIError(f"Unrecognised response from {config.AI_NAME}: {json.dumps(data)[:300]}")


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AIError("AI did not return JSON")
    return json.loads(m.group(0))


SYSTEM = (
    "You are the DeployTrack assistant for the UW Facilities (UWF) tech team, which is running a "
    "computer refresh: replacing ~240 staff laptops/desktops in phases. Workflow statuses: Verify → "
    "Follow Up → Replace → Needs Scheduling (user emailed a Bookings link, reminders sent) → "
    "Scheduled → Deployed (old device still to collect) → Complete. 'No Replace - …' means no new "
    "device. Answer only from the data provided. Be concise and practical. Always mention deployment "
    "IDs (like D123) next to people's names so the team can click through. Today is {today}."
)


def _today():
    d = datetime.now(workflow.LOCAL_TZ)
    return f"{d:%A, %B} {d.day}, {d.year}"


def _row_line(r):
    sched = workflow.fmt_dt(r["scheduled_start"]) if r["scheduled_start"] else ""
    return " | ".join([
        r["id"], r["user_name"] or "(shared)", r["department"], f"P{r['phase']}", r["status"],
        r["tech"] or "-", sched or "-", r["req_number"] or "no ticket",
        f"reminders={r['reminder_count']}" + (" escalated" if r["escalated_at"] else ""),
        f"asked={r['scheduling_requested_at'][:10]}" if r["scheduling_requested_at"] else "",
        f"old device returned={r['old_device_returned'] or '-'}",
        (r["notes"] or "").replace("\n", " ")[:120],
    ])


def _context(rows, summary):
    active = [r for r in rows if not r["status"].startswith("No Replace")]
    nr = len(rows) - len(active)
    head = "ID | user | dept | phase | status | tech | scheduled | ticket | reminders | asked | old device | notes"
    return (f"SUMMARY: {json.dumps(summary)}\n\n"
            f"{nr} deployments are 'No Replace' and omitted.\n\nDEPLOYMENTS:\n{head}\n"
            + "\n".join(_row_line(r) for r in active))


# ------------------------------------------------------------------ features

def ask(question: str, rows, summary, history=None) -> dict:
    if config.AI_PROVIDER == "offline":
        return {"answer": _offline_ask(question, rows), "provider": "offline"}
    msgs = [{"role": "user", "content": _context(rows, summary)},
            {"role": "assistant", "content": "Got it — I have the current deployment data."}]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:2000]})
    msgs.append({"role": "user", "content": question})
    answer = _complete(SYSTEM.format(today=_today()), msgs)
    return {"answer": answer.strip(), "provider": config.AI_PROVIDER}


def draft_followup(dep, activity) -> dict:
    if config.AI_PROVIDER == "offline":
        return _offline_followup(dep)
    hist = "\n".join(f"- {a['ts'][:10]} {a['kind']}: {a['message']}" for a in reversed(activity[:15]))
    prompt = (
        f"Write a short, warm, personal follow-up email to {dep['user_name']} ({dep['department']}). "
        f"They were asked to book a setup appointment for their new {dep['deployment_type'].lower()} "
        f"and have not booked after {dep['reminder_count']} reminder(s). Booking link: {config.BOOKINGS_URL}. "
        f"Use the notes and history to make it personal and make booking easy (e.g. offer to come to "
        f"them or suggest times that fit their notes). Under 120 words. Sign off as {config.TEAM_NAME}.\n\n"
        f"Notes: {dep['notes'] or 'none'}\nHistory:\n{hist}\n\n"
        'Return JSON: {"subject": "...", "body": "..."}'
    )
    out = _complete(SYSTEM.format(today=_today()), [{"role": "user", "content": prompt}], FOLLOWUP_SCHEMA)
    return {"subject": out.get("subject", ""), "body": out.get("body", ""), "provider": config.AI_PROVIDER}


REPLY_HINT = (
    '{"summary": "one sentence", '
    '"suggested_status": "one of the statuses, or empty string if no change", '
    '"scheduled_start": "ISO 8601 local datetime if they proposed a specific time, else empty string", '
    '"note": "short note to log on the record", '
    '"reply_subject": "...", "reply_body": "short friendly reply email", '
    '"needs_human": true/false, "reason": "why"}'
)


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


FOLLOWUP_SCHEMA = _obj({"subject": {"type": "string"}, "body": {"type": "string"}})
REPLY_SCHEMA = _obj({
    "summary": {"type": "string"},
    "suggested_status": {"type": "string", "enum": workflow.STATUSES + [""]},
    "scheduled_start": {"type": "string"},
    "note": {"type": "string"},
    "reply_subject": {"type": "string"},
    "reply_body": {"type": "string"},
    "needs_human": {"type": "boolean"},
    "reason": {"type": "string"},
})


def interpret_reply(dep, text: str) -> dict:
    if config.AI_PROVIDER == "offline":
        return _offline_interpret(dep, text)
    prompt = (
        f"A user replied to our scheduling emails. Read it and suggest what the tech team should do.\n"
        f"Record: {_row_line(dep)}\nBooking link: {config.BOOKINGS_URL}\n"
        f"Valid statuses: {', '.join(workflow.STATUSES)}\n\n"
        f"User's reply:\n\"\"\"\n{text}\n\"\"\"\n\n"
        f"If they propose a specific day/time, suggest status Scheduled with that time (the team will "
        f"approve it) and write a reply confirming it. If they say they left, retired or don't need a "
        f"device, suggest the matching No Replace status. Sign the reply as {config.TEAM_NAME}. "
        f"Return only JSON: {REPLY_HINT}"
    )
    out = _complete(SYSTEM.format(today=_today()), [{"role": "user", "content": prompt}], REPLY_SCHEMA)
    if out.get("suggested_status") not in workflow.STATUSES:
        out["suggested_status"] = None
    out["scheduled_start"] = out.get("scheduled_start") or None
    out["provider"] = config.AI_PROVIDER
    return out


# ------------------------------------------------------------------ offline fallbacks
# Simple rules so the demo still works without an AI connection. Clearly labelled in the UI.

def _people(rows, limit=12):
    parts = [f"{r['user_name'] or '(shared)'} ({r['id']})" for r in rows[:limit]]
    more = f", and {len(rows) - limit} more" if len(rows) > limit else ""
    return ", ".join(parts) + more


def _offline_ask(q, rows):
    ql = q.lower()
    m = re.search(r"phase\s*(\d)", ql)
    if m:
        rows = [r for r in rows if r["phase"] == m.group(1)]
    techs = {(r["tech"] or "").strip().lower() for r in rows if r["tech"]}
    tech = next((t for t in techs if re.search(rf"\b{re.escape(t)}\b", ql)), None)
    if tech:
        rows = [r for r in rows if (r["tech"] or "").strip().lower() == tech]
    scope = (f"Phase {m.group(1)}" if m else "all phases") + (f", {tech.title()}" if tech else "")
    now = datetime.now(workflow.LOCAL_TZ)

    def when(r):
        return workflow.parse_dt(r["scheduled_start"])

    if any(k in ql for k in ("escalat", "not respond", "ignor", "ghost", "unresponsive")):
        sel = [r for r in rows if r["status"] == "Needs Scheduling" and r["escalated_at"]]
        return f"{len(sel)} people ({scope}) haven't responded after all reminders: {_people(sel) or 'none'}."
    if any(k in ql for k in ("today", "tomorrow", "this week", "upcoming", "coming up")):
        days = 0 if "today" in ql and "tomorrow" not in ql else 1 if "tomorrow" in ql else 7
        end = (now + timedelta(days=days)).date()
        sel = sorted([r for r in rows if r["status"] == "Scheduled" and when(r)
                      and when(r).astimezone(workflow.LOCAL_TZ).date() <= end], key=when)
        lines = [f"• {workflow.fmt_dt(r['scheduled_start'])} — {r['user_name']} ({r['id']}), tech {r['tech'] or '?'}"
                 for r in sel]
        return f"{len(sel)} deployments scheduled ({scope}):\n" + ("\n".join(lines) or "none")
    if any(k in ql for k in ("old device", "collect", "returned", "outstanding")):
        sel = [r for r in rows if r["status"] == "Deployed" and r["old_device_returned"].lower() != "yes"]
        return f"{len(sel)} old devices still to collect ({scope}): {_people(sel) or 'none'}."
    if "ticket" in ql:
        sel = [r for r in rows if r["ticket_pending"]]
        return f"{len(sel)} deployments still need a Service Dashboard ticket: {_people(sel) or 'none'}."
    if "schedul" in ql or "book" in ql:
        sel = sorted([r for r in rows if r["status"] == "Needs Scheduling"],
                     key=lambda r: r["scheduling_requested_at"])
        return (f"{len(sel)} people ({scope}) have been sent the Bookings link but haven't booked yet. "
                f"Waiting longest: {_people(sel) or 'none'}.")
    for st in workflow.STATUSES:
        if st.lower() in ql:
            sel = [r for r in rows if r["status"] == st]
            return f"{len(sel)} deployments ({scope}) are '{st}': {_people(sel) or 'none'}."
    for r in rows:
        if r["user_name"] and any(len(p) > 2 and re.search(rf"\b{re.escape(p.lower())}\b", ql)
                                  for p in r["user_name"].split()[1:]):
            extra = f", scheduled {workflow.fmt_dt(r['scheduled_start'])}" if r["scheduled_start"] else ""
            return (f"{r['user_name']} ({r['id']}) — {r['status']}{extra}. Tech: {r['tech'] or 'unassigned'}. "
                    f"Ticket: {r['req_number'] or 'none yet'}. Notes: {r['notes'] or 'none'}.")
    active = [r for r in rows if not r["status"].startswith("No Replace")]
    done = [r for r in active if r["status"] in ("Deployed", "Complete")]
    counts = {}
    for r in active:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    pct = round(100 * len(done) / len(active)) if active else 0
    return (f"Progress ({scope}): {len(done)} of {len(active)} devices deployed ({pct}%). "
            + ", ".join(f"{v} {k}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) + ".")


def _offline_followup(dep):
    first = (dep["user_name"] or "there").split()[0]
    extra = ""
    notes = (dep["notes"] or "").lower()
    if "tue" in notes or "thu" in notes:
        extra = " Since you're on site Tuesdays and Thursdays, we can come to your desk on either day."
    elif "remote" in notes:
        extra = " If it's easier, we can plan around the days you're on site."
    return {
        "subject": f"Quick one: your new computer is still waiting for you ({dep['id']})",
        "body": (f"Hi {first},\n\nI know inboxes get busy, so I'll keep this short: your new "
                 f"{dep['deployment_type'].lower() or 'computer'} is ready and setup takes about an hour.{extra}\n\n"
                 f"You can grab any open slot here: {config.BOOKINGS_URL}\n"
                 f"Or just reply with a day and time that works, and we'll book it for you.\n\n"
                 f"Thanks,\n{config.TEAM_NAME}"),
        "provider": "offline",
    }


_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]


def _offline_interpret(dep, text):
    t = text.lower()
    first = (dep["user_name"] or "there").split()[0]
    sign = f"\n\nThanks,\n{config.TEAM_NAME}"
    if any(k in t for k in ("retir", "last day", "leaving uw", "no longer", "resign")):
        st = "No Replace - Retired" if "retir" in t else "No Replace - Resigned"
        return {"summary": "User is leaving and won't need the new device.", "suggested_status": st,
                "scheduled_start": None, "note": f"User replied: leaving ({st.split(' - ')[1].lower()}). Collect old device.",
                "reply_subject": "Thanks for letting us know",
                "reply_body": f"Hi {first},\n\nThanks for letting us know, and best of luck! We'll reach out about collecting your current device before your last day.{sign}",
                "needs_human": True, "reason": "Confirm with supervisor before cancelling the deployment.",
                "provider": "offline"}
    day = next((d for d in _DAYS if d in t), None)
    hour = 9 if "morning" in t else 13 if "afternoon" in t else None
    hm = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t)
    if hm:
        hour = int(hm.group(1)) % 12 + (12 if hm.group(3) == "pm" else 0)
    start = None
    if day:
        now = datetime.now(workflow.LOCAL_TZ)
        back = re.search(r"back (?:on )?(\w+ \d{1,2})", t)
        base = now
        if back:
            for fmt in ("%B %d", "%b %d"):
                try:
                    b = datetime.strptime(back.group(1).title(), fmt).replace(year=now.year, tzinfo=workflow.LOCAL_TZ)
                    base = max(now, b - timedelta(days=1))
                    break
                except ValueError:
                    continue
        delta = (_DAYS.index(day) - base.weekday()) % 7 or 7
        dt = (base + timedelta(days=delta)).replace(hour=hour or 10, minute=int(hm.group(2) or 0) if hm else 0,
                                                    second=0, microsecond=0)
        start = dt.isoformat()
    if start:
        nice = workflow.fmt_dt(start)
        return {"summary": f"User wants to book {nice}.", "suggested_status": "Scheduled",
                "scheduled_start": start, "note": f"User replied by email asking for {nice}.",
                "reply_subject": f"You're booked: {nice}",
                "reply_body": f"Hi {first},\n\nThanks for getting back to us! We've booked your setup for {nice}. Please bring your current device{' and accessories' if dep['accessories'] else ''}.{sign}",
                "needs_human": False, "reason": "Specific time requested.", "provider": "offline"}
    return {"summary": "User replied but didn't give a specific time.", "suggested_status": None,
            "scheduled_start": None, "note": f"User replied: \"{text.strip()[:140]}\"",
            "reply_subject": "Re: your new computer setup",
            "reply_body": f"Hi {first},\n\nThanks for the reply! Whenever you're ready, you can pick a time here: {config.BOOKINGS_URL}\nOr reply with a day and time and we'll book it for you.{sign}",
            "needs_human": True, "reason": "No time found in the reply.", "provider": "offline"}
