/* DeployTrack web demo: the server's workflow, running in the browser on fake data.
   Mirrors app/workflow.py, app/demo_data.py and app/ai.py. Each viewer gets their own copy,
   saved in their browser. AI uses Claude through the artifact's `sample` capability when the
   viewer allows it, otherwise the same offline rules as the server. */
const Backend = (() => {
  const CFG = { team: "UWF Tech Team", bookings: "https://outlook.office.com/bookwithme/uwf-tech-team", interval: 3, maxRem: 2 };
  const PIPELINE = ["Verify", "Follow Up", "Replace", "Needs Scheduling", "Scheduled", "Deployed", "Complete"];
  const NO_REPLACE = ["No Replace - Action Needed", "No Replace - Duplicate", "No Replace - Not UWF", "No Replace - Operational", "No Replace - Resigned", "No Replace - Retired", "No Replace - Surplus"];
  const STATUSES = PIPELINE.concat(NO_REPLACE);
  const EDITABLE = ["phase", "req_number", "tech", "shared_device", "deployment_type", "new_device_sn", "dubsupport_tag", "accessories", "location", "user_name", "netid", "user_email", "dept_contact", "dept_contact_email", "department", "old_device_sn", "last_login", "old_device_returned", "notes", "scheduled_start", "scheduled_end"];
  const KEY = "deploytrack-web-demo-v1";
  const DAY = 864e5;

  let S = null;
  let sampleFn = null;      // Claude, when the viewer's app provides it
  let aiDeclined = false;   // viewer said no: stay on offline rules for this view

  const clone = (o) => JSON.parse(JSON.stringify(o));
  const iso = (d) => new Date(d).toISOString().replace(/\.\d{3}Z$/, "Z");
  const nowIso = () => iso(Date.now());
  const pd = (v) => { if (!v) return null; const d = new Date(v); return isNaN(d) ? null : d; };
  function save() { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch {} }
  function load() { try { const j = localStorage.getItem(KEY); if (j) { S = JSON.parse(j); return true; } } catch {} return false; }
  class WorkflowError extends Error {}

  function fmtDt(v) {
    const d = pd(v); if (!d) return v || "";
    const day = d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
    const t = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
    return `${day} at ${t}`;
  }

  /* ------------------------------------------------------------ records & log */
  const dep = (id) => S.deps[id] || null;
  function log(id, kind, message, actor = "system", ts) {
    S.activity.push({ id: ++S.seq.a, deployment_id: id, ts: ts || nowIso(), kind, actor, message });
  }
  function update(id, fields) { Object.assign(S.deps[id], fields, { updated_at: nowIso() }); }
  function sendEmail(id, to, subject, body, cc = "", actor = "system", ts) {
    S.outbox.push({ id: ++S.seq.m, ts: ts || nowIso(), deployment_id: id, to_addr: to, cc, subject, body, delivered: 0, error: "" });
    log(id, "email", `Email to ${to}${cc ? " (cc " + cc + ")" : ""}: "${subject}" — saved to outbox`, actor, ts);
  }

  /* ------------------------------------------------------------ email templates */
  const first = (d) => (d.user_name || d.netid || "there").split(" ")[0];
  const ref = (d) => d.id + (d.req_number ? " / " + d.req_number : "");
  const T = {
    request: (d) => [`Your new computer is ready — please schedule your setup (${ref(d)})`,
      `Hi ${first(d)},\n\nAs part of the 2026 UWF Computer Devices Refresh, your new ${(d.deployment_type || "device").toLowerCase()} is ready for you.\n\nPlease book a setup appointment with us here:\n${CFG.bookings}\n\nThe appointment takes about an hour. Please bring your current device${d.accessories ? " and accessories" : ""} so we can transfer your setup and collect the old one.\n\nQuestions? Just reply to this email.\n\nThanks,\n${CFG.team}`],
    reminder: (d, n) => [`Reminder: please schedule your new computer setup (${ref(d)})`,
      `Hi ${first(d)},\n\nJust a friendly reminder (#${n}) that your new computer is ready and waiting. Please pick a time that works for you:\n${CFG.bookings}\n\nIf none of the times work, reply to this email and we will find a time.\n\nThanks,\n${CFG.team}`],
    escalation: (d) => [`Help needed: ${d.user_name || d.netid} has not scheduled their computer refresh`,
      `Hi ${(d.dept_contact || "there").trim()},\n\nWe have contacted ${d.user_name || d.netid} (${d.department}) ${d.reminder_count + 1} times about scheduling the setup for their new computer, but haven't received a booking yet.\n\nCould you help us connect with them? They can book here:\n${CFG.bookings}\n\nReference: ${ref(d)}\n\nThanks,\n${CFG.team}`],
    confirm: (d) => [`Confirmed: computer setup on ${fmtDt(d.scheduled_start)} (${ref(d)})`,
      `Hi ${first(d)},\n\nThanks for booking! We'll see you on ${fmtDt(d.scheduled_start)}${d.location ? " at " + d.location : ""}.\n\nPlease bring your current device${d.accessories ? " and accessories" : ""}.\n\nThanks,\n${CFG.team}`],
    rebook: (d) => [`Your computer setup was cancelled — please rebook (${ref(d)})`,
      `Hi ${first(d)},\n\nYour setup appointment was cancelled. When you're ready, please choose a new time:\n${CFG.bookings}\n\nThanks,\n${CFG.team}`],
  };

  function ticketText(d) {
    return {
      short_description: `Device refresh deployment ${d.id} — ${d.user_name || d.netid}`,
      description: `Computer refresh deployment for ${d.user_name} (${d.netid}).\nDepartment: ${d.department}\nDeployment type: ${d.deployment_type}\nNew device SN: ${d.new_device_sn || "TBD"}\nOld device SN: ${d.old_device_sn || "TBD"}\nAccessories: ${d.accessories}\nLocation: ${d.location}\nTracker ID: ${d.id}`,
      assigned_to: d.tech, requested_for: d.netid,
    };
  }

  /* ------------------------------------------------------------ workflow */
  function setStatus(id, st, actor = "system", reason = "", notify = true) {
    if (!STATUSES.includes(st)) throw new WorkflowError(`Unknown status '${st}'.`);
    let d = dep(id); if (!d) throw new WorkflowError(`Deployment ${id} not found`);
    const old = d.status;
    if (old === st) return d;
    if (st === "Needs Scheduling" && !d.user_email) throw new WorkflowError("Add the user's email before moving to Needs Scheduling.");
    update(id, { status: st });
    log(id, "status", `${old || "(none)"} → ${st}${reason ? " (" + reason + ")" : ""}`, actor);
    d = dep(id);
    if (st === "Needs Scheduling") {
      if (!d.req_number) {
        log(id, "ticket", "Ticket needed: create it in the Service Dashboard, then paste the REQ# here.", actor);
        update(id, { ticket_pending: 1 });
      }
      const [s, b] = old === "Scheduled" ? T.rebook(d) : T.request(d);
      sendEmail(id, d.user_email, s, b, "", actor);
      const now = nowIso();
      update(id, { scheduling_requested_at: d.scheduling_requested_at || now, last_contacted_at: now, reminder_count: 0, escalated_at: "", scheduled_start: "", scheduled_end: "" });
    } else if (st === "Scheduled" && notify && d.scheduled_start && d.user_email) {
      const [s, b] = T.confirm(d); sendEmail(id, d.user_email, s, b, "", actor);
    } else if (st === "Deployed" && (d.old_device_returned || "").toLowerCase() === "yes") {
      return setStatus(id, "Complete", actor, "old device already returned");
    } else if (st === "Complete" && d.req_number) {
      log(id, "ticket", `Close ${d.req_number} in the Service Dashboard.`, actor);
      update(id, { close_ticket_pending: 1 });
    }
    return dep(id);
  }

  function updateDeployment(id, fields, actor = "user") {
    const d = dep(id); if (!d) throw new WorkflowError(`Deployment ${id} not found`);
    const ch = {};
    for (const [k, v] of Object.entries(fields)) if (EDITABLE.includes(k) && v != null && String(v) !== String(d[k])) ch[k] = v;
    const before = clone(d);
    if (ch.req_number) ch.ticket_pending = 0;
    update(id, ch);
    for (const k of Object.keys(ch)) if (k !== "ticket_pending") log(id, "edit", `${k.replace(/_/g, " ")}: '${before[k]}' → '${ch[k]}'`, actor);
    if ("old_device_returned" in ch && String(ch.old_device_returned).toLowerCase() === "yes" && dep(id).status === "Deployed") setStatus(id, "Complete", actor, "old device returned");
    return dep(id);
  }

  function handleBooking(event, email, start, end, depId, actor = "bookings") {
    let rows;
    if (depId) rows = dep(depId) ? [dep(depId)] : [];
    else {
      const e = (email || "").trim().toLowerCase();
      if (!e) throw new WorkflowError("Booking needs customer_email or deployment_id");
      rows = Object.values(S.deps).filter((r) => (r.user_email.toLowerCase() === e || r.netid.toLowerCase() === e.split("@")[0]) && ["Needs Scheduling", "Scheduled"].includes(r.status));
    }
    const out = [];
    for (const d of rows) {
      if (event === "created" || event === "updated") {
        update(d.id, { scheduled_start: start, scheduled_end: end || "" });
        log(d.id, "booking", `Booked for ${fmtDt(start)}`, actor);
        if (d.status === "Scheduled") { const [s, b] = T.confirm(dep(d.id)); sendEmail(d.id, d.user_email, s, b, "", actor); }
        else setStatus(d.id, "Scheduled", actor, "booked in Bookings");
      } else if (event === "cancelled") {
        log(d.id, "booking", "Booking cancelled", actor);
        if (d.status === "Scheduled") setStatus(d.id, "Needs Scheduling", actor, "booking cancelled");
      }
      out.push(d.id);
    }
    return out;
  }

  function runReminders(daysAhead = 0, actor = "reminder-bot") {
    const now = Date.now() + daysAhead * DAY, cutoff = now - CFG.interval * DAY;
    const res = { reminded: [], escalated: [], needs_contact_email: [] };
    for (const d of Object.values(S.deps)) {
      if (d.status !== "Needs Scheduling" || d.escalated_at) continue;
      const last = pd(d.last_contacted_at) || pd(d.scheduling_requested_at);
      if (last && last.getTime() > cutoff) continue;
      const stamp = iso(now);
      if (d.reminder_count < CFG.maxRem) {
        const n = d.reminder_count + 1, [s, b] = T.reminder(d, n);
        sendEmail(d.id, d.user_email, s, b, "", actor);
        update(d.id, { reminder_count: n, last_contacted_at: stamp }); res.reminded.push(d.id);
      } else if (d.dept_contact_email) {
        const [s, b] = T.escalation(d);
        sendEmail(d.id, d.dept_contact_email, s, b, d.user_email, actor);
        update(d.id, { escalated_at: stamp, last_contacted_at: stamp }); res.escalated.push(d.id);
      } else {
        log(d.id, "reminder", "No response after reminders and no department contact email — follow up manually.", actor);
        update(d.id, { escalated_at: stamp }); res.needs_contact_email.push(d.id);
      }
    }
    return res;
  }

  /* ------------------------------------------------------------ views */
  const byIdAsc = (a, b) => a.id.localeCompare(b.id);
  function todo() {
    const rows = Object.values(S.deps).sort(byIdAsc), items = [];
    rows.filter((d) => d.ticket_pending).forEach((d) => items.push({ kind: "create_ticket", deployment_id: d.id, title: `Create Service Dashboard ticket for ${d.user_name || d.netid}`, ticket: ticketText(d) }));
    rows.filter((d) => d.close_ticket_pending).forEach((d) => items.push({ kind: "close_ticket", deployment_id: d.id, title: `Close ${d.req_number} (${d.user_name})` }));
    rows.filter((d) => d.status === "Needs Scheduling" && d.escalated_at).forEach((d) => items.push({ kind: "unresponsive", deployment_id: d.id, title: `${d.user_name || d.netid} hasn't booked after reminders` }));
    rows.filter((d) => d.status === "Deployed" && !["yes", "n/a"].includes((d.old_device_returned || "").toLowerCase())).forEach((d) => items.push({ kind: "collect_device", deployment_id: d.id, title: `Collect old device from ${d.user_name || d.netid}` }));
    rows.filter((d) => ["Needs Scheduling", "Replace"].includes(d.status) && !d.user_email).forEach((d) => items.push({ kind: "missing_email", deployment_id: d.id, title: `Add email for ${d.user_name || d.id}` }));
    const end = new Date(); end.setDate(end.getDate() + 1); end.setHours(23, 59, 59);
    rows.filter((d) => d.status === "Scheduled" && pd(d.scheduled_start) && pd(d.scheduled_start) <= end).forEach((d) => items.push({ kind: "upcoming", deployment_id: d.id, title: `Deployment ${fmtDt(d.scheduled_start)}: ${d.user_name} (${d.tech || "no tech assigned"})` }));
    return items;
  }

  function summary(rows = Object.values(S.deps)) {
    const by = (f) => rows.reduce((m, r) => { const k = f(r); m[k] = (m[k] || 0) + 1; return m; }, {});
    const active = rows.filter((r) => !r.status.startsWith("No Replace"));
    const done = rows.filter((r) => ["Deployed", "Complete"].includes(r.status)).length;
    return {
      total: rows.length, by_status: by((r) => r.status), by_phase: by((r) => r.phase || "(none)"),
      to_replace: active.length, deployed_or_complete: done,
      percent_done: active.length ? Math.round((1000 * done) / active.length) / 10 : 0,
      waiting_on_user: rows.filter((r) => r.status === "Needs Scheduling").length,
    };
  }

  const LIFECYCLE = [["verifying", "Verifying", ["Verify", "Follow Up"]], ["ready", "Ready to send", ["Replace"]], ["waiting", "Waiting on user", ["Needs Scheduling"]], ["scheduled", "Scheduled", ["Scheduled"]], ["deployed", "Deployed", ["Deployed"]], ["complete", "Complete", ["Complete"]]];
  function insights(phase) {
    let rows = Object.values(S.deps);
    if (phase) rows = rows.filter((r) => r.phase === phase);
    const ids = new Set(rows.map((r) => r.id));
    const acts = S.activity.filter((a) => ids.has(a.deployment_id)).sort((a, b) => a.id - b.id);
    const entered = {};
    for (const a of acts) if (a.kind === "status" && a.message.includes("→ ")) {
      const st = a.message.split("→ ")[1].split(" (")[0].trim();
      (entered[a.deployment_id] = entered[a.deployment_id] || {});
      if (!entered[a.deployment_id][st]) entered[a.deployment_id][st] = a.ts;
    }
    const avg = (a, b) => {
      const spans = Object.values(entered).filter((e) => e[a] && e[b]).map((e) => (pd(e[b]) - pd(e[a])) / DAY).filter((x) => x >= 0);
      return spans.length ? Math.round((10 * spans.reduce((x, y) => x + y, 0)) / spans.length) / 10 : null;
    };
    const now = new Date(), w0 = new Date(now); w0.setHours(0, 0, 0, 0); w0.setDate(w0.getDate() - ((w0.getDay() + 6) % 7));
    const weeks = Array.from({ length: 8 }, (_, i) => new Date(w0.getTime() - (7 - i) * 7 * DAY));
    const weekly = new Array(8).fill(0);
    for (const e of Object.values(entered)) {
      const ts = pd(e.Deployed); if (!ts || ts > now) continue;
      for (let i = 7; i >= 0; i--) if (ts >= weeks[i]) { weekly[i]++; break; }
    }
    const phases = {};
    for (const r of rows) {
      if (r.status.startsWith("No Replace")) continue;
      const p = (phases[r.phase || "—"] = phases[r.phase || "—"] || { done: 0, in_progress: 0, not_started: 0 });
      if (["Deployed", "Complete"].includes(r.status)) p.done++; else if (["Verify", "Follow Up"].includes(r.status)) p.not_started++; else p.in_progress++;
    }
    const waiting = rows.filter((r) => r.status === "Needs Scheduling");
    const pad = (n) => String(n).padStart(2, "0");
    return {
      stages: LIFECYCLE.map(([key, label, sts]) => ({ key, label, statuses: sts, count: rows.filter((r) => sts.includes(r.status)).length })),
      no_replace: rows.filter((r) => r.status.startsWith("No Replace")).length,
      avg_days: { request_to_booked: avg("Needs Scheduling", "Scheduled"), booked_to_deployed: avg("Scheduled", "Deployed"), deployed_to_complete: avg("Deployed", "Complete"), request_to_complete: avg("Needs Scheduling", "Complete") },
      weekly_deployed: weeks.map((w, i) => ({ week: `${w.getFullYear()}-${pad(w.getMonth() + 1)}-${pad(w.getDate())}`, count: weekly[i] })),
      phases: Object.keys(phases).sort().map((k) => ({ phase: k, ...phases[k] })),
      waiting_escalated: waiting.filter((r) => r.escalated_at).length,
      waiting_reminded: waiting.filter((r) => r.reminder_count).length,
      automation: { emails: acts.filter((a) => a.kind === "email").length, bookings_synced: acts.filter((a) => a.kind === "booking").length, tickets_queued: acts.filter((a) => a.kind === "ticket").length },
    };
  }

  const byTsDesc = (a, b) => (b.ts > a.ts ? 1 : b.ts < a.ts ? -1 : b.id - a.id);
  function list(p) {
    let rows = Object.values(S.deps).sort(byIdAsc);
    const st = p.get("status"), ph = p.get("phase"), q = (p.get("q") || "").toLowerCase();
    if (st === "active") rows = rows.filter((r) => PIPELINE.slice(0, -1).includes(r.status));
    else if (st === "no-replace") rows = rows.filter((r) => r.status.startsWith("No Replace"));
    else if (st) rows = rows.filter((r) => r.status.toLowerCase() === st.toLowerCase());
    if (ph) rows = rows.filter((r) => r.phase === ph);
    if (q) rows = rows.filter((r) => [r.id, r.user_name, r.netid, r.department, r.req_number, r.new_device_sn, r.old_device_sn, r.notes].join(" ").toLowerCase().includes(q));
    return rows;
  }
  function get(id) {
    const d = dep(id); if (!d) throw new WorkflowError(`Deployment ${id} not found`);
    return { ...d, activity: S.activity.filter((a) => a.deployment_id === id).sort(byTsDesc), emails: S.outbox.filter((m) => m.deployment_id === id).sort(byTsDesc) };
  }

  /* ------------------------------------------------------------ demo data */
  function rng(seed) { return () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  const FIRST = ["Maria", "James", "Priya", "Daniel", "Aisha", "Kevin", "Sofia", "Marcus", "Elena", "Tom", "Grace", "Omar", "Hannah", "Luis", "Mei", "Robert", "Fatima", "Nathan", "Chloe", "Victor", "Ingrid", "Samuel", "Leah", "Andre", "Yuki", "Brian", "Nadia", "Carlos", "Erin", "Ravi", "Olivia", "Hassan", "Megan", "Diego", "Julia", "Kofi", "Rachel", "Theo", "Lena", "Isaac"];
  const LAST = ["Lopez", "Nguyen", "Patel", "Kim", "Johnson", "Okafor", "Garcia", "Chen", "Rossi", "Walsh", "Haddad", "Larsen", "Brooks", "Silva", "Tanaka", "Murphy", "Ahmed", "Fischer", "Reyes", "Cole", "Novak", "Grant", "Bauer", "Mensah", "Ito", "Price", "Kowalski", "Ortiz", "Hayes", "Sharma"];
  const DEPTS = [["Project Delivery Group", "Dana Whitfield"], ["Architecture", "Jordan Ellis"], ["Building Environmental Controls", "Casey Morgan"], ["Finance and Administration", "Riley Chen"], ["Business Intelligence", "Alex Romero"], ["Facilities Maintenance", "Sam Patel"], ["Grounds & Landscaping", "Taylor Brooks"], ["Custodial Services", "Morgan Lee"], ["Capital Projects", "Jamie Ortiz"], ["Transportation Services", "Avery Kim"]];
  const TECHS = ["Alex", "Bea", "Chris", "Dev", "Jo", "Kai"];
  const NOTES = ["Prefers mornings", "Hybrid — on site Tue/Thu", "Needs a 2nd monitor cable", "Wants to keep current dock", "Uses CAD software — confirm license transfer", "Shares office with two others", "Remote most days, can come to T13", ""];
  const PLAN = [["0", "No Replace - Duplicate", 12], ["0", "No Replace - Surplus", 9], ["0", "No Replace - Retired", 6], ["0", "No Replace - Operational", 6], ["0", "Complete", 7], ["1", "Complete", 38], ["1", "Deployed", 14], ["1", "No Replace - Resigned", 2], ["1", "Follow Up", 1], ["2", "Verify", 38], ["2", "Follow Up", 7], ["2", "Replace", 14], ["2", "Needs Scheduling", 26], ["2", "Scheduled", 13], ["2", "Deployed", 9], ["2", "Complete", 9], ["2", "No Replace - Action Needed", 2], ["3", "Verify", 22]];

  function seed() {
    const R = rng(7), pick = (a) => a[Math.floor(R() * a.length)], ri = (a, b) => a + Math.floor(R() * (b - a + 1));
    const now = Date.now();
    S = { deps: {}, activity: [], outbox: [], seq: { a: 0, m: 0 }, seededAt: nowIso() };
    const slot = (t) => { const d = new Date(t); while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1); d.setHours(pick([9, 10, 11, 13, 14, 15]), pick([0, 30]), 0, 0); return d.getTime(); };
    const past = (t) => (t > now ? now - ri(20, 300) * 6e4 : t);
    const add = (d, kind, msg, t, actor = "system") => log(d.id, kind, msg, actor, iso(past(t)));
    const mail = (d, to, [s, b], t, cc = "") => sendEmail(d.id, to, s, b, cc, "system", iso(past(t)));
    const used = new Set(); let n = 100, req = 9100000, schedI = 0;
    const sn = (k) => Array.from({ length: k }, () => pick("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ".split(""))).join("");
    const rows = [];
    for (const [phase, status, count] of PLAN) for (let c = 0; c < count; c++) {
      let name; do { name = `${pick(FIRST)} ${pick(LAST)}`; } while (used.has(name)); used.add(name);
      const [fn, ln] = name.split(" "), netid = (fn[0] + ln).toLowerCase().slice(0, 8) + ri(1, 9);
      const [dept, contact] = pick(DEPTS), laptop = R() < 0.62;
      const shared = R() < 0.12 && (status.startsWith("No Replace") || ["Verify", "Follow Up"].includes(status));
      n++;
      const d = {
        id: "D" + n, phase, status, deployment_type: laptop ? "Standard Laptop" : "Standard Desktop",
        accessories: laptop ? "Dock, Keyboard/Mouse, Video Cables" : "Keyboard/Mouse", location: pick(["T13 Hybrid", "T13", "FPB", "Plant Services"]),
        shared_device: shared ? "Yes" : "No", user_name: shared ? "" : name, netid: shared ? "" : netid,
        user_email: shared ? "" : `${fn}.${ln}@example.edu`.toLowerCase(), department: dept, dept_contact: contact,
        dept_contact_email: `${contact.split(" ")[0]}.${contact.split(" ")[1]}@example.edu`.toLowerCase(),
        old_device_sn: sn(10), last_login: netid, notes: pick(NOTES), dubsupport_tag: "", tech: "", req_number: "", new_device_sn: "",
        old_device_returned: "", scheduled_start: "", scheduled_end: "", scheduling_requested_at: "", last_contacted_at: "",
        reminder_count: 0, escalated_at: "", ticket_pending: 0, close_ticket_pending: 0, created_at: nowIso(), updated_at: nowIso(),
      };
      if (status === "Follow Up") d.notes = pick(["Confirm user is still in department", "Waiting on supervisor approval", "Device not seen in DubSupport for 60+ days"]);
      if (!(status.startsWith("No Replace") || ["Verify", "Follow Up"].includes(status))) { d.tech = pick(TECHS); d.new_device_sn = sn(7); d.dubsupport_tag = "TRUE"; }
      rows.push(d); S.deps[d.id] = d;
    }
    for (const d of rows) {
      const st = d.status;
      add(d, "system", "Imported from tracker CSV", now - ri(40, 70) * DAY);
      if (!["Needs Scheduling", "Scheduled", "Deployed", "Complete"].includes(st)) continue;
      const asked = now - (st === "Needs Scheduling" ? ri(1, 15) : st === "Scheduled" ? ri(9, 16) : ri(8, 56)) * DAY;
      d.scheduling_requested_at = d.last_contacted_at = iso(asked);
      const hasTicket = !(st === "Needs Scheduling" && R() < 0.3);
      if (hasTicket) { req += ri(3, 400); d.req_number = "REQ0" + req; }
      add(d, "status", "Replace → Needs Scheduling", asked, d.tech);
      mail(d, d.user_email, T.request(d), asked);
      if (hasTicket) add(d, "edit", `req number: '' → '${d.req_number}'`, asked + 2 * 36e5, d.tech);
      else { d.ticket_pending = 1; add(d, "ticket", "Ticket needed: create it in the Service Dashboard, then paste the REQ# here.", asked); }
      if (st === "Needs Scheduling") {
        const age = Math.floor((now - asked) / DAY), rem = Math.min(CFG.maxRem, Math.floor(age / CFG.interval));
        let last = asked;
        for (let i = 1; i <= rem; i++) { last = asked + CFG.interval * i * DAY; mail(d, d.user_email, T.reminder(d, i), last); }
        d.reminder_count = rem; d.last_contacted_at = iso(past(last));
        if (rem >= CFG.maxRem && age > CFG.interval * (rem + 1)) {
          const e = asked + CFG.interval * (rem + 1) * DAY;
          mail(d, d.dept_contact_email, T.escalation(d), e, d.user_email);
          d.escalated_at = d.last_contacted_at = iso(past(e));
        }
        continue;
      }
      const booked = asked + ri(1, 4) * DAY;
      let start;
      if (st === "Scheduled") { start = slot(now + [0, 0, 1, 1, 2, 3, 3, 4, 5, 6, 7, 8, 9][schedI++ % 13] * DAY); }
      else { start = slot(booked + ri(2, 8) * DAY); if (start > now - 3 * 36e5) { start = slot(now - ri(1, 3) * DAY); while (start > now - 3 * 36e5) start -= DAY; } }
      d.scheduled_start = iso(start); d.scheduled_end = iso(start + 36e5);
      add(d, "booking", `Booked for ${fmtDt(d.scheduled_start)}`, booked, "bookings");
      add(d, "status", "Needs Scheduling → Scheduled (booked in Bookings)", booked, "bookings");
      mail(d, d.user_email, T.confirm(d), booked);
      if (st === "Deployed" || st === "Complete") {
        add(d, "status", "Scheduled → Deployed", start + 36e5, d.tech);
        d.old_device_returned = st === "Complete" ? "Yes" : "No";
        if (st === "Complete") {
          add(d, "edit", "old device returned: 'No' → 'Yes'", start + ri(0, 5) * DAY, d.tech);
          add(d, "status", "Deployed → Complete (old device returned)", start + DAY, d.tech);
          add(d, "ticket", `Ticket ${d.req_number} closed in Service Dashboard`, start + DAY, d.tech);
        }
      }
    }
    rows.filter((r) => r.status === "Complete").slice(0, 3).forEach((r) => (r.close_ticket_pending = 1));
    save();
    return { deployments: rows.length };
  }

  /* ------------------------------------------------------------ AI */
  const aiLive = () => !!sampleFn && !aiDeclined;
  const todayStr = () => new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  const RULES = () => `You are the DeployTrack assistant for the UW Facilities (UWF) tech team, which is replacing ~240 staff laptops and desktops in phases. Workflow statuses: Verify → Follow Up → Replace → Needs Scheduling (user emailed a Bookings link, reminders sent automatically) → Scheduled → Deployed (old device still to collect) → Complete. "No Replace - …" means no new device. Answer only from the data provided, concisely and practically, in plain text without Markdown tables or headings. Always put deployment IDs (like D123) next to people's names. Today is ${todayStr()}. All people in this data are fictional demo records.`;
  function rowLine(r) {
    return [r.id, r.user_name || "(shared)", r.department, "P" + r.phase, r.status, r.tech || "-", r.scheduled_start ? fmtDt(r.scheduled_start) : "-", r.req_number || "no ticket",
      `reminders=${r.reminder_count}${r.escalated_at ? " escalated" : ""}`, r.scheduling_requested_at ? "asked=" + r.scheduling_requested_at.slice(0, 10) : "",
      "old device returned=" + (r.old_device_returned || "-"), (r.notes || "").slice(0, 120)].join(" | ");
  }
  function context() {
    const rows = Object.values(S.deps).sort(byIdAsc), active = rows.filter((r) => !r.status.startsWith("No Replace"));
    return `SUMMARY: ${JSON.stringify(summary())}\n\n${rows.length - active.length} deployments are "No Replace" and omitted.\n\nDEPLOYMENTS:\nID | user | dept | phase | status | tech | scheduled | ticket | reminders | asked | old device | notes\n${active.map(rowLine).join("\n")}`;
  }
  function onAiError(e) {
    if (["not_granted", "sampling_disabled", "not_declared", "capability_disabled", "capability_removed"].includes(e && e.code)) { aiDeclined = true; return true; }
    return false;
  }

  async function ask(question, history, onText) {
    if (aiLive()) {
      const turns = [{ role: "user", content: RULES() + "\n\n" + context() }];
      (history || []).slice(-6).forEach((h) => turns.push({ role: h.role, content: String(h.content).slice(0, 2000) }));
      turns.push({ role: "user", content: question });
      try {
        const r = await sampleFn(turns, { cache: false, onText: ({ text }) => onText && onText(text) });
        return { answer: r.text, provider: "claude" };
      } catch (e) {
        if (!onAiError(e)) throw new Error(e.code === "rate_limited" ? "Claude is busy for you right now. Try again in a minute." : "Claude couldn't answer that. Try again.");
      }
    }
    return { answer: offlineAsk(question), provider: "offline" };
  }

  async function followup(id) {
    const d = get(id);
    if (aiLive()) {
      const hist = d.activity.slice(0, 15).reverse().map((a) => `- ${a.ts.slice(0, 10)} ${a.kind}: ${a.message}`).join("\n");
      const prompt = `${RULES()}\n\nWrite a short, warm, personal follow-up email to ${d.user_name} (${d.department}). They were asked to book a setup appointment for their new ${d.deployment_type.toLowerCase()} and have not booked after ${d.reminder_count} reminder(s). Booking link: ${CFG.bookings}. Use the notes and history to make it personal and make booking easy (offer to come to them, or suggest times that fit their notes). Under 120 words. Sign off as ${CFG.team}.\n\nNotes: ${d.notes || "none"}\nHistory:\n${hist}\n\nReply with only JSON: {"subject": "...", "body": "..."}`;
      try {
        const out = await sampleFn.json(prompt, { modelTier: "quick", cache: false });
        if (out && out.subject && out.body) return { subject: String(out.subject), body: String(out.body), provider: "claude" };
      } catch (e) { onAiError(e); }
    }
    return offlineFollowup(d);
  }

  async function interpret(id, text) {
    const d = get(id);
    if (aiLive()) {
      const prompt = `${RULES()}\n\nA user replied to our scheduling emails. Read it and suggest what the tech team should do.\nRecord: ${rowLine(d)}\nBooking link: ${CFG.bookings}\nValid statuses: ${STATUSES.join(", ")}\n\nUser's reply:\n"""\n${text}\n"""\n\nIf they propose a specific day and time, suggest status Scheduled with that time as a local ISO datetime (like 2026-11-24T10:00) on the next such date after today, and write a reply confirming it. If they say they left, retired or don't need a device, suggest the matching No Replace status. Sign the reply as ${CFG.team}.\nReply with only JSON: {"summary": "one sentence", "suggested_status": "a status or empty string", "scheduled_start": "local ISO datetime or empty string", "note": "short note to log", "reply_subject": "...", "reply_body": "short friendly reply", "needs_human": true or false, "reason": "why"}`;
      try {
        const out = await sampleFn.json(prompt, { modelTier: "quick", cache: false });
        if (out && typeof out === "object") {
          const ss = pd(out.scheduled_start);
          return { summary: String(out.summary || ""), suggested_status: STATUSES.includes(out.suggested_status) ? out.suggested_status : null,
            scheduled_start: ss ? iso(ss) : null, note: String(out.note || ""), reply_subject: String(out.reply_subject || ""), reply_body: String(out.reply_body || ""),
            needs_human: !!out.needs_human, reason: String(out.reason || ""), provider: "claude" };
        }
      } catch (e) { onAiError(e); }
    }
    return offlineInterpret(d, text);
  }

  /* offline rules: the same logic as the server's fallback */
  const people = (rows, lim = 12) => rows.slice(0, lim).map((r) => `${r.user_name || "(shared)"} (${r.id})`).join(", ") + (rows.length > lim ? `, and ${rows.length - lim} more` : "");
  function offlineAsk(q) {
    const ql = q.toLowerCase(); let rows = Object.values(S.deps).sort(byIdAsc);
    const m = ql.match(/phase\s*(\d)/); if (m) rows = rows.filter((r) => r.phase === m[1]);
    const techs = [...new Set(rows.map((r) => (r.tech || "").toLowerCase()).filter(Boolean))];
    const tech = techs.find((t) => new RegExp("\\b" + t + "\\b").test(ql)); if (tech) rows = rows.filter((r) => (r.tech || "").toLowerCase() === tech);
    const scope = (m ? "Phase " + m[1] : "all phases") + (tech ? ", " + tech[0].toUpperCase() + tech.slice(1) : "");
    if (/(escalat|not respond|ignor|ghost|unresponsive)/.test(ql)) { const s = rows.filter((r) => r.status === "Needs Scheduling" && r.escalated_at); return `${s.length} people (${scope}) haven't responded after all reminders: ${people(s) || "none"}.`; }
    if (/(today|tomorrow|this week|upcoming|coming up|schedule)/.test(ql) && !/(hasn|haven|not|need)/.test(ql)) {
      const days = ql.includes("today") && !ql.includes("tomorrow") ? 0 : ql.includes("tomorrow") ? 1 : 7;
      const end = new Date(); end.setDate(end.getDate() + days); end.setHours(23, 59, 59);
      const s = rows.filter((r) => r.status === "Scheduled" && pd(r.scheduled_start) && pd(r.scheduled_start) <= end).sort((a, b) => pd(a.scheduled_start) - pd(b.scheduled_start));
      return `${s.length} deployments scheduled (${scope}):\n` + (s.map((r) => `• ${fmtDt(r.scheduled_start)} — ${r.user_name} (${r.id}), tech ${r.tech || "?"}`).join("\n") || "none");
    }
    if (/(old device|collect|returned|outstanding)/.test(ql)) { const s = rows.filter((r) => r.status === "Deployed" && (r.old_device_returned || "").toLowerCase() !== "yes"); return `${s.length} old devices still to collect (${scope}): ${people(s) || "none"}.`; }
    if (ql.includes("ticket")) { const s = rows.filter((r) => r.ticket_pending); return `${s.length} deployments still need a Service Dashboard ticket: ${people(s) || "none"}.`; }
    if (/(schedul|book)/.test(ql)) { const s = rows.filter((r) => r.status === "Needs Scheduling").sort((a, b) => a.scheduling_requested_at.localeCompare(b.scheduling_requested_at)); return `${s.length} people (${scope}) have been sent the Bookings link but haven't booked yet. Waiting longest: ${people(s) || "none"}.`; }
    for (const st of STATUSES) if (ql.includes(st.toLowerCase())) { const s = rows.filter((r) => r.status === st); return `${s.length} deployments (${scope}) are '${st}': ${people(s) || "none"}.`; }
    for (const r of rows) if (r.user_name && r.user_name.split(" ").slice(1).some((p) => p.length > 2 && new RegExp("\\b" + p.toLowerCase() + "\\b").test(ql)))
      return `${r.user_name} (${r.id}) — ${r.status}${r.scheduled_start ? ", scheduled " + fmtDt(r.scheduled_start) : ""}. Tech: ${r.tech || "unassigned"}. Ticket: ${r.req_number || "none yet"}. Notes: ${r.notes || "none"}.`;
    const active = rows.filter((r) => !r.status.startsWith("No Replace")), done = active.filter((r) => ["Deployed", "Complete"].includes(r.status));
    const c = {}; active.forEach((r) => (c[r.status] = (c[r.status] || 0) + 1));
    return `Progress (${scope}): ${done.length} of ${active.length} devices deployed (${active.length ? Math.round((100 * done.length) / active.length) : 0}%). ` + Object.entries(c).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${v} ${k}`).join(", ") + ".";
  }
  function offlineFollowup(d) {
    const n = (d.notes || "").toLowerCase();
    const extra = n.includes("tue") || n.includes("thu") ? " Since you're on site Tuesdays and Thursdays, we can come to your desk on either day." : n.includes("remote") ? " If it's easier, we can plan around the days you're on site." : "";
    return { subject: `Quick one: your new computer is still waiting for you (${d.id})`,
      body: `Hi ${first(d)},\n\nI know inboxes get busy, so I'll keep this short: your new ${(d.deployment_type || "computer").toLowerCase()} is ready and setup takes about an hour.${extra}\n\nYou can grab any open slot here: ${CFG.bookings}\nOr just reply with a day and time that works, and we'll book it for you.\n\nThanks,\n${CFG.team}`, provider: "offline" };
  }
  const DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
  function offlineInterpret(d, text) {
    const t = text.toLowerCase(), fn = first(d), sign = `\n\nThanks,\n${CFG.team}`;
    if (/(retir|last day|leaving uw|no longer|resign)/.test(t)) {
      const st = t.includes("retir") ? "No Replace - Retired" : "No Replace - Resigned";
      return { summary: "User is leaving and won't need the new device.", suggested_status: st, scheduled_start: null, note: "User replied: leaving. Collect old device.", reply_subject: "Thanks for letting us know",
        reply_body: `Hi ${fn},\n\nThanks for letting us know, and best of luck! We'll reach out about collecting your current device before your last day.${sign}`, needs_human: true, reason: "Confirm with their supervisor before cancelling the deployment.", provider: "offline" };
    }
    const day = DAYS.slice(1, 6).find((x) => t.includes(x));
    let hour = t.includes("morning") ? 9 : t.includes("afternoon") ? 13 : null, min = 0;
    const hm = t.match(/\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b/);
    if (hm) { hour = (parseInt(hm[1], 10) % 12) + (hm[3] === "pm" ? 12 : 0); min = parseInt(hm[2] || "0", 10); }
    if (day) {
      const dt = new Date(); const delta = ((DAYS.indexOf(day) - dt.getDay() + 7) % 7) || 7;
      dt.setDate(dt.getDate() + delta); dt.setHours(hour ?? 10, min, 0, 0);
      const nice = fmtDt(dt);
      return { summary: `User wants to book ${nice}.`, suggested_status: "Scheduled", scheduled_start: iso(dt), note: `User replied by email asking for ${nice}.`, reply_subject: `You're booked: ${nice}`,
        reply_body: `Hi ${fn},\n\nThanks for getting back to us! We've booked your setup for ${nice}. Please bring your current device${d.accessories ? " and accessories" : ""}.${sign}`, needs_human: false, reason: "Specific time requested.", provider: "offline" };
    }
    return { summary: "User replied but didn't give a specific time.", suggested_status: null, scheduled_start: null, note: `User replied: "${text.trim().slice(0, 140)}"`, reply_subject: "Re: your new computer setup",
      reply_body: `Hi ${fn},\n\nThanks for the reply! Whenever you're ready, you can pick a time here: ${CFG.bookings}\nOr reply with a day and time and we'll book it for you.${sign}`, needs_human: true, reason: "No time found in the reply.", provider: "offline" };
  }

  function applyReply(id, b) {
    const d = dep(id); if (!d) throw new WorkflowError(`Deployment ${id} not found`);
    if (b.reply_text) log(id, "note", `User replied: "${b.reply_text.trim().slice(0, 500)}"`, b.actor);
    if (b.note) log(id, "note", b.note, b.actor);
    if (b.status === "Scheduled" && b.scheduled_start) update(id, { scheduled_start: b.scheduled_start, scheduled_end: "" });
    if (b.status && b.status !== d.status) setStatus(id, b.status, b.actor, "from user's reply", !(b.send_reply && b.reply_body));
    if (b.send_reply && b.reply_body && d.user_email) { sendEmail(id, d.user_email, b.reply_subject || "Re: your new computer", b.reply_body, "", b.actor); update(id, { last_contacted_at: nowIso() }); }
    return dep(id);
  }

  /* ------------------------------------------------------------ router (same paths as the server) */
  async function request(path, opts = {}) {
    const u = new URL(path, "https://deploytrack.local"), p = u.pathname, q = u.searchParams, m = (opts.method || "GET").toUpperCase();
    const b = opts.json || {};
    let seg;
    const W = (fn) => { const r = fn(); save(); return clone(r); };
    try {
      if (p === "/api/meta") return { statuses: STATUSES, pipeline: PIPELINE, no_replace: NO_REPLACE, demo_mode: true, demo_db: true, email_mode: "outbox", snow_mode: "manual", redirect_all_email_to: "", reminder_interval_days: CFG.interval, max_reminders: CFG.maxRem, bookings_url: CFG.bookings, ai: aiStatus() };
      if (p === "/api/deployments" && m === "GET") return clone(list(q));
      if (p === "/api/deployments/bulk-status") return W(() => { const res = { ok: [], errors: {} }; for (const id of b.ids) { try { setStatus(id, b.status, b.actor); res.ok.push(id); } catch (e) { res.errors[id] = e.message; } } return res; });
      if ((seg = p.match(/^\/api\/deployments\/([^/]+)$/))) return m === "PATCH" ? W(() => updateDeployment(seg[1], b, b.actor)) : clone(get(seg[1]));
      if ((seg = p.match(/^\/api\/deployments\/([^/]+)\/status$/))) return W(() => setStatus(seg[1], b.status, b.actor, b.reason || ""));
      if ((seg = p.match(/^\/api\/deployments\/([^/]+)\/notes$/))) return W(() => { if (!dep(seg[1])) throw new WorkflowError("Not found"); log(seg[1], "note", b.text, b.actor); return { ok: true }; });
      if ((seg = p.match(/^\/api\/deployments\/([^/]+)\/ticket-closed$/))) return W(() => { update(seg[1], { close_ticket_pending: 0 }); log(seg[1], "ticket", "Ticket closed in Service Dashboard", q.get("actor") || "web"); return { ok: true }; });
      if ((seg = p.match(/^\/api\/deployments\/([^/]+)\/email$/))) return W(() => { const d = dep(seg[1]); sendEmail(d.id, d.user_email, b.subject, b.body, "", b.actor); update(d.id, { last_contacted_at: nowIso() }); return { ok: true }; });
      if (p === "/api/summary") return summary();
      if (p === "/api/insights") return insights(q.get("phase") || "");
      if (p === "/api/todo") return clone(todo());
      if (p === "/api/outbox") return clone([...S.outbox].sort(byTsDesc).slice(0, +(q.get("limit") || 200)));
      if (p === "/api/activity") return clone([...S.activity].sort(byTsDesc).slice(0, +(q.get("limit") || 100)));
      if (p === "/api/reminders/run") return W(() => runReminders(+(q.get("days_ahead") || 0)));
      if (p === "/api/bookings/webhook") return W(() => ({ matched: handleBooking(b.event, b.customer_email, b.start, b.end, b.deployment_id) }));
      if ((seg = p.match(/^\/api\/ai\/followup\/([^/]+)$/))) return await followup(seg[1]);
      if ((seg = p.match(/^\/api\/ai\/reply\/([^/]+)$/))) return await interpret(seg[1], b.text);
      if ((seg = p.match(/^\/api\/ai\/reply\/([^/]+)\/apply$/))) return W(() => applyReply(seg[1], b));
      if (p === "/api/demo/reset") return seed();
    } catch (e) {
      throw new Error(e.message || "Something went wrong");
    }
    throw new Error("Unknown request " + p);
  }

  function aiStatus() { return aiLive() ? { provider: "claude", live: true, ready: true, name: "Claude", model: "" } : { provider: "offline", live: false, ready: true, name: "AI", model: "" }; }

  function init() { if (!load() || !S || !S.deps) seed(); }
  function setSample(fn) { sampleFn = fn; }
  return { init, request, ask, setSample, aiStatus, ticketText, isAiDeclined: () => aiDeclined };
})();
