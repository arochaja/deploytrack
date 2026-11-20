"use strict";

/* ===================================================================== helpers */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const slug = (s) => (s || "").toLowerCase().startsWith("no replace") ? "no-replace" : (s || "").toLowerCase().replace(/\s+/g, "-");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const initials = (n) => (n || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("") || "?";
const firstName = (n) => (n || "there").split(" ")[0];

const state = { meta: null, rows: [], selected: new Set(), current: null, insights: null, lastFeedId: 0 };
const ORDER = { "Verify": 0, "Follow Up": 0, "Replace": 1, "Needs Scheduling": 2, "Scheduled": 3, "Deployed": 4, "Complete": 5 };

function getKey() { try { return localStorage.getItem("dt-key") || ""; } catch { return ""; } }
function setKey(k) { try { localStorage.setItem("dt-key", k); } catch {} }

async function api(path, opts = {}, retried = false) {
  const headers = Object.assign({}, opts.headers || {});
  if (getKey()) headers["X-API-Key"] = getKey();
  let body = opts.body;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.json); }
  const res = await fetch(path, { method: opts.method || "GET", headers, body });
  if (res.status === 401 && !retried) {
    const k = prompt("This DeployTrack needs an API key:");
    if (k) { setKey(k.trim()); return api(path, opts, true); }
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}

function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (isError ? " error" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), isError ? 6000 : 3200);
}
async function guard(fn) { try { return await fn(); } catch (e) { toast(e.message, true); } }

const dt = (v) => { const d = new Date(v); return v && !isNaN(d) ? d : null; };
const fmtDate = (v) => { const d = dt(v); return d ? d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : (v || ""); };
const fmtDay = (v) => { const d = dt(v); return d ? d.toLocaleDateString([], { month: "short", day: "numeric" }) : ""; };
const fmtTime = (v) => { const d = dt(v); return d ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : ""; };
const daysSince = (v) => { const d = dt(v); return d ? Math.max(0, Math.floor((Date.now() - d) / 864e5)) : 0; };
const fmtAgo = (v) => {
  const d = dt(v); if (!d) return v || "";
  const s = (Date.now() - d) / 1000;
  if (s < 0) return fmtDay(v);
  if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  if (s < 86400 * 7) return Math.floor(s / 86400) + "d ago";
  return fmtDay(v);
};
function relDay(v) {
  const d = dt(v); if (!d) return "";
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((new Date(d).setHours(0, 0, 0, 0) - today) / 864e5);
  if (diff === 0) return "Today"; if (diff === 1) return "Tomorrow"; if (diff === -1) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

const ICON = {
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></svg>',
  box: '<svg viewBox="0 0 24 24"><path d="M3 7l9-4 9 4-9 4-9-4z"/><path d="M3 7v10l9 4 9-4V7"/><path d="M12 11v10"/></svg>',
  send: '<svg viewBox="0 0 24 24"><path d="M21 3L10 14"/><path d="M21 3l-7 18-4-7-7-4 18-7z"/></svg>',
  cal: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>',
  laptop: '<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="11" rx="1.5"/><path d="M2 19h20"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7"/></svg>',
  bell: '<svg viewBox="0 0 24 24"><path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4l2-2z"/><path d="M10 21h4"/></svg>',
  ret: '<svg viewBox="0 0 24 24"><path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/></svg>',
};
const FEED_IC = { status: "➜", email: "✉", booking: "📅", ticket: "🎫", note: "✎", edit: "✎", reminder: "🔔", system: "•" };

/* ===================================================================== tooltip */
function showTip(e, html) {
  const t = $("#tip"); t.innerHTML = html; t.classList.remove("hidden");
  const pad = 12, w = t.offsetWidth, h = t.offsetHeight;
  let x = e.clientX + pad, y = e.clientY - h - pad;
  if (x + w > innerWidth - 8) x = e.clientX - w - pad;
  if (y < 8) y = e.clientY + pad;
  t.style.left = x + "px"; t.style.top = y + "px";
}
function hideTip() { $("#tip").classList.add("hidden"); }

/* ===================================================================== tabs */
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$$("[data-goto]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.goto)));
function showTab(name) {
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  if (name === "overview") loadOverview();
  if (name === "board") renderBoard();
  if (name === "todo") loadTodo();
  if (name === "outbox") loadOutbox();
  if (name === "activity") loadActivity();
}
const activeTab = () => ($(".tab.active") || {}).id;

/* ===================================================================== data */
async function loadAll() {
  state.rows = await api("/api/deployments?limit=5000");
  fillFilterOptions();
  renderTable();
  if (activeTab() === "tab-overview") await loadOverview();
  if (activeTab() === "tab-board") renderBoard();
  if (activeTab() === "tab-todo") await loadTodo(); else refreshTodoCount();
}

function fillFilterOptions() {
  const phases = [...new Set(state.rows.map((r) => r.phase).filter(Boolean))].sort();
  const techs = [...new Set(state.rows.map((r) => (r.tech || "").trim()).filter(Boolean))].sort();
  const keep = (sel, opts, first, fmt = (o) => o) => {
    const v = sel.value;
    sel.innerHTML = first + opts.map((o) => `<option value="${esc(o)}">${esc(fmt(o))}</option>`).join("");
    sel.value = v;
  };
  ["#fPhase", "#bPhase", "#ovPhase"].forEach((s) => keep($(s), phases, '<option value="">All phases</option>', (p) => "Phase " + p));
  keep($("#fTech"), techs, '<option value="">All techs</option>');
  const st = $("#fStatus");
  if (st.options.length <= 3) st.innerHTML += state.meta.statuses.map((s) => `<option>${esc(s)}</option>`).join("");
}

/* ===================================================================== overview */
const STAGE_META = {
  verifying: { icon: ICON.search, who: "Team verifies" },
  ready: { icon: ICON.box, who: "1 click to send" },
  waiting: { icon: ICON.send, who: "⚡ Auto reminders", auto: true },
  scheduled: { icon: ICON.cal, who: "User books online" },
  deployed: { icon: ICON.laptop, who: "Tech sets up" },
  complete: { icon: ICON.check, who: "⚡ Auto ticket close", auto: true, done: true },
};
const CONNECTORS = [
  { label: "verify", manual: true },
  { label: "⚡ email + ticket" },
  { label: "⚡ user books", avg: "request_to_booked" },
  { label: "setup visit", manual: true, avg: "booked_to_deployed" },
  { label: "⚡ device back", avg: "deployed_to_complete" },
];

async function loadOverview() {
  const phase = $("#ovPhase").value;
  const [ins, acts] = await Promise.all([api("/api/insights" + (phase ? "?phase=" + phase : "")), api("/api/activity?limit=8")]);
  state.insights = ins;
  renderFlow(ins); renderKpis(ins); renderWeekly(ins.weekly_deployed); renderPhases(ins.phases);
  renderAgenda(); renderFeed(acts);
}
$("#ovPhase").addEventListener("change", loadOverview);

function renderFlow(ins) {
  const total = ins.stages.reduce((a, s) => a + s.count, 0) || 1;
  $("#flow").innerHTML = ins.stages.map((s, i) => {
    const m = STAGE_META[s.key], c = CONNECTORS[i];
    const avg = c && c.avg && ins.avg_days[c.avg] != null ? `${ins.avg_days[c.avg]} day${ins.avg_days[c.avg] === 1 ? "" : "s"} avg` : "";
    return `<button class="stage ${m.auto ? "auto" : ""} ${m.done ? "done" : ""}" data-stage="${s.key}" aria-label="${esc(s.label)}: ${s.count}">
      ${c ? `<div class="conn"><span class="${c.manual ? "manual" : ""}">${esc(c.label)}</span></div>${avg ? `<div class="conn below"><span>${avg}</span></div>` : ""}` : ""}
      <div class="node">${m.icon}</div>
      <div class="num">${s.count}</div>
      <div class="lbl">${esc(s.label)}</div>
      <div class="who">${esc(m.who)}</div>
      <div class="bar"><i style="width:${Math.round((100 * s.count) / total)}%"></i></div>
    </button>`;
  }).join("");
  $$("#flow .stage").forEach((b) => {
    const s = ins.stages.find((x) => x.key === b.dataset.stage);
    b.addEventListener("mousemove", (e) => showTip(e, `<b>${esc(s.label)}</b><br>${s.count} devices · ${s.statuses.join(", ")}<br>Click to see them`));
    b.addEventListener("mouseleave", hideTip);
    b.addEventListener("click", () => { hideTip(); goToStage(s); });
  });
}

function goToStage(s) {
  if (s.key === "verifying") {
    $("#fStatus").value = "Verify"; showTab("deployments"); renderTable(); return;
  }
  showTab("board");
  const col = $(`.col[data-key="${s.key}"]`);
  if (col) { col.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" }); col.animate([{ boxShadow: "0 0 0 3px var(--accent)" }, { boxShadow: "0 0 0 0 transparent" }], { duration: 1400 }); }
}

function ring(pct) {
  const r = 24, c = 2 * Math.PI * r;
  return `<svg class="ring" viewBox="0 0 58 58" aria-hidden="true"><circle class="track" cx="29" cy="29" r="${r}"/><circle class="val" cx="29" cy="29" r="${r}" stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - pct / 100)}"/></svg>`;
}

function renderKpis(ins) {
  const st = Object.fromEntries(ins.stages.map((s) => [s.key, s.count]));
  const active = ins.stages.reduce((a, s) => a + s.count, 0);
  const done = st.deployed + st.complete;
  const pct = active ? Math.round((100 * done) / active) : 0;
  const outstanding = state.rows.filter((r) => r.status === "Deployed" && !["yes", "n/a"].includes((r.old_device_returned || "").toLowerCase())).length;
  const a = ins.avg_days;
  $("#kpis").innerHTML = `
    <div class="kpi hero"><div class="k-label">Refresh complete</div><div class="k-value">${pct}<small>%</small></div><div class="k-sub">${done} of ${active} devices deployed</div>${ring(pct)}</div>
    <div class="kpi"><div class="k-label">Request → booked</div><div class="k-value">${a.request_to_booked ?? "—"}<small>days</small></div><div class="k-sub">${a.request_to_complete ?? "—"} days request → complete</div></div>
    <div class="kpi"><div class="k-label">Waiting on users</div><div class="k-value">${st.waiting}</div><div class="k-sub">${ins.waiting_reminded} reminded · ${ins.waiting_escalated} escalated</div></div>
    <div class="kpi"><div class="k-label">Old devices to collect</div><div class="k-value">${outstanding}</div><div class="k-sub">deployed, not yet returned</div></div>
    <div class="kpi"><div class="k-label">Emails sent automatically</div><div class="k-value">${ins.automation.emails}</div><div class="k-sub">${ins.automation.bookings_synced} bookings synced, 0 spreadsheet edits</div></div>`;
}

function renderWeekly(data) {
  const W = 560, H = 210, L = 30, R = 8, T = 18, B = 26;
  const max = Math.max(4, ...data.map((d) => d.count));
  const step = max <= 8 ? 2 : max <= 20 ? 5 : 10;
  const top = Math.ceil(max / step) * step;
  const band = (W - L - R) / data.length, bw = Math.min(36, band * 0.56);
  const y = (v) => T + (H - T - B) * (1 - v / top);
  let grid = "", bars = "", hits = "", xl = "";
  for (let v = 0; v <= top; v += step) grid += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 3.5}" text-anchor="end">${v}</text>`;
  data.forEach((d, i) => {
    const x = L + band * i + (band - bw) / 2, y0 = y(0), yt = y(d.count), r = Math.min(4, (y0 - yt));
    if (d.count > 0) bars += `<path class="bar-mark" data-i="${i}" d="M${x},${y0} V${yt + r} Q${x},${yt} ${x + r},${yt} H${x + bw - r} Q${x + bw},${yt} ${x + bw},${yt + r} V${y0} Z"/>`;
    hits += `<rect class="hit" data-i="${i}" x="${L + band * i}" y="${T}" width="${band}" height="${H - T - B}"/>`;
    xl += `<text x="${L + band * i + band / 2}" y="${H - 8}" text-anchor="middle">${fmtDay(d.week + "T12:00:00")}</text>`;
  });
  const last = data[data.length - 1], li = data.length - 1;
  const lastLbl = `<text class="val-label" x="${L + band * li + band / 2}" y="${y(last.count) - 6}" text-anchor="middle">${last.count}</text>`;
  const total = data.reduce((a, d) => a + d.count, 0);
  $("#weeklyChart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Devices deployed per week, ${total} in 8 weeks">
    <g class="grid axis">${grid}</g><g>${bars}</g>${lastLbl}<g class="axis">${xl}</g><g>${hits}</g></svg>`;
  $$("#weeklyChart .hit").forEach((h) => {
    const i = +h.dataset.i, d = data[i];
    h.addEventListener("mousemove", (e) => {
      $$("#weeklyChart .bar-mark").forEach((b) => b.classList.toggle("dim", +b.dataset.i !== i));
      showTip(e, `<b>Week of ${fmtDay(d.week + "T12:00:00")}</b><br>${d.count} devices deployed`);
    });
    h.addEventListener("mouseleave", () => { $$("#weeklyChart .bar-mark").forEach((b) => b.classList.remove("dim")); hideTip(); });
  });
}

function renderPhases(phases) {
  const segs = [["done", "Done", "var(--seq-3)"], ["in_progress", "In progress", "var(--seq-2)"], ["not_started", "Not started", "var(--seq-1)"]];
  $("#phaseLegend").innerHTML = segs.map(([, l, c]) => `<span><i style="background:${c}"></i>${l}</span>`).join("");
  $("#phaseChart").innerHTML = phases.map((p) => {
    const tot = p.done + p.in_progress + p.not_started || 1;
    return `<div class="phase-row"><div class="pl">Phase ${esc(p.phase)}</div>
      <div class="stack">${segs.filter(([k]) => p[k] > 0).map(([k, l, c]) => `<div data-tip="<b>Phase ${esc(p.phase)} · ${l}</b><br>${p[k]} of ${tot} devices" style="flex:${p[k]};background:${c}"></div>`).join("")}</div>
      <div class="pv"><b>${p.done}</b> / ${tot} done</div></div>`;
  }).join("") || '<div class="empty">No data</div>';
  $$("#phaseChart .stack > div").forEach((s) => {
    s.addEventListener("mousemove", (e) => { $$("#phaseChart .stack > div").forEach((o) => (o.style.opacity = o === s ? 1 : 0.4)); showTip(e, s.dataset.tip); });
    s.addEventListener("mouseleave", () => { $$("#phaseChart .stack > div").forEach((o) => (o.style.opacity = 1)); hideTip(); });
  });
}

function renderAgenda() {
  const now = new Date(), end = new Date(); end.setDate(end.getDate() + 1); end.setHours(23, 59, 59);
  let rows = state.rows.filter((r) => r.status === "Scheduled" && dt(r.scheduled_start)).sort((a, b) => dt(a.scheduled_start) - dt(b.scheduled_start));
  let soon = rows.filter((r) => dt(r.scheduled_start) <= end && dt(r.scheduled_start) >= new Date(now.getTime() - 12 * 36e5));
  if (!soon.length) soon = rows.slice(0, 5);
  $("#agenda").innerHTML = soon.slice(0, 6).map((r) => `<div class="agenda-item" data-id="${esc(r.id)}">
      <div class="a-time">${esc(fmtTime(r.scheduled_start))}<small>${esc(relDay(r.scheduled_start))}</small></div>
      <span class="avatar">${esc(initials(r.user_name))}</span>
      <div style="min-width:0"><div class="a-name">${esc(r.user_name || r.id)}</div><div class="muted small">${esc(r.department)} · ${esc(r.location || "")} · tech ${esc(r.tech || "?")}</div></div>
    </div>`).join("") || '<div class="empty">Nothing scheduled.</div>';
  $$("#agenda .agenda-item").forEach((el) => el.addEventListener("click", () => openDrawer(el.dataset.id)));
}

function feedItem(a, showDep = true, isNew = false) {
  return `<li class="k-${esc(a.kind)} ${isNew ? "new" : ""}"><span class="ic">${FEED_IC[a.kind] || "•"}</span><div>
    <div class="msg">${showDep ? `<span class="dep" data-id="${esc(a.deployment_id)}">${esc(a.deployment_id)}</span> · ` : ""}${esc(a.message)}</div>
    <div class="meta">${esc(a.actor)} · ${esc(fmtAgo(a.ts))}</div></div></li>`;
}
function bindDeps(root) { $$(".dep", root).forEach((el) => el.addEventListener("click", () => openDrawer(el.dataset.id))); }
function renderFeed(acts) {
  const prev = state.lastFeedId;
  $("#feed").innerHTML = acts.map((a) => feedItem(a, true, prev && a.id > prev)).join("") || '<li class="empty">No activity yet.</li>';
  state.lastFeedId = Math.max(prev, ...acts.map((a) => a.id));
  bindDeps($("#feed"));
}
setInterval(() => {
  if (activeTab() === "tab-overview" && !document.hidden) api("/api/activity?limit=8").then(renderFeed).catch(() => {});
}, 6000);

/* ===================================================================== board */
const BOARD = [
  { key: "ready", title: "Ready to send", statuses: ["Replace"] },
  { key: "waiting", title: "Waiting on user", statuses: ["Needs Scheduling"], auto: "⚡ reminders on" },
  { key: "scheduled", title: "Scheduled", statuses: ["Scheduled"] },
  { key: "deployed", title: "Deployed", statuses: ["Deployed"] },
  { key: "complete", title: "Complete", statuses: ["Complete"], limit: 15 },
];
$("#bq").addEventListener("input", renderBoard);
$("#bPhase").addEventListener("change", renderBoard);

function boardCard(r) {
  const tags = [];
  if (r.status === "Needs Scheduling") {
    const d = daysSince(r.scheduling_requested_at);
    tags.push(`<span class="tag ${r.escalated_at ? "bad" : d >= 6 ? "warn" : ""}">Day ${d}</span>`);
    if (r.reminder_count) tags.push(`<span class="tag">🔔 ${r.reminder_count}</span>`);
    if (r.escalated_at) tags.push('<span class="tag bad">No response</span>');
  }
  if (r.status === "Scheduled" && r.scheduled_start) tags.push(`<span class="tag acc">${esc(relDay(r.scheduled_start))} ${esc(fmtTime(r.scheduled_start))}</span>`);
  if (r.status === "Deployed") tags.push('<span class="tag warn">Collect old device</span>');
  if (r.status === "Complete") tags.push('<span class="tag ok">✓ Done</span>');
  if (r.ticket_pending) tags.push('<span class="tag info">Needs ticket</span>');
  if (r.close_ticket_pending) tags.push('<span class="tag info">Close ticket</span>');
  return `<button class="kcard" data-id="${esc(r.id)}">
    <div class="top"><span class="avatar">${esc(initials(r.user_name))}</span><div style="min-width:0"><div class="nm">${esc(r.user_name || "(shared device)")}</div><div class="dp">${esc(r.department)}</div></div></div>
    ${tags.length ? `<div class="tags">${tags.join("")}</div>` : ""}
    <div class="foot"><span>${esc(r.id)} · ${esc((r.deployment_type || "").replace("Standard ", ""))}</span><span>${esc(r.tech || "unassigned")}</span></div>
  </button>`;
}

function renderBoard() {
  const q = $("#bq").value.trim().toLowerCase(), ph = $("#bPhase").value;
  const rows = state.rows.filter((r) => (!ph || r.phase === ph) &&
    (!q || [r.id, r.user_name, r.department, r.netid, r.tech].join(" ").toLowerCase().includes(q)));
  $("#board").innerHTML = BOARD.map((c) => {
    let items = rows.filter((r) => c.statuses.includes(r.status));
    if (c.key === "waiting") items.sort((a, b) => (a.scheduling_requested_at || "").localeCompare(b.scheduling_requested_at || ""));
    if (c.key === "scheduled") items.sort((a, b) => (a.scheduled_start || "").localeCompare(b.scheduled_start || ""));
    if (c.key === "complete") items.sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
    const shown = c.limit ? items.slice(0, c.limit) : items;
    return `<div class="col" data-key="${c.key}"><div class="col-head"><h3>${esc(c.title)} <span class="n">${items.length}</span></h3>${c.auto ? `<span class="auto-tag">${c.auto}</span>` : ""}</div>
      <div class="col-body">${shown.map(boardCard).join("") || '<div class="empty">None</div>'}${items.length > shown.length ? `<div class="more">+ ${items.length - shown.length} more</div>` : ""}</div></div>`;
  }).join("");
  $$("#board .kcard").forEach((el) => el.addEventListener("click", () => openDrawer(el.dataset.id)));
}

/* ===================================================================== table */
function filtered() {
  const q = $("#q").value.trim().toLowerCase();
  const ph = $("#fPhase").value, tech = $("#fTech").value, st = $("#fStatus").value;
  return state.rows.filter((r) => {
    if (ph && r.phase !== ph) return false;
    if (tech && (r.tech || "").trim() !== tech) return false;
    if (st === "active" && !state.meta.pipeline.slice(0, -1).includes(r.status)) return false;
    if (st === "no-replace" && !r.status.startsWith("No Replace")) return false;
    if (st && st !== "active" && st !== "no-replace" && r.status !== st) return false;
    if (q && ![r.id, r.user_name, r.netid, r.department, r.req_number, r.new_device_sn, r.old_device_sn, r.notes, r.last_login].join(" ").toLowerCase().includes(q)) return false;
    return true;
  });
}
function renderTable() {
  const rows = filtered();
  $("#table tbody").innerHTML = rows.map((r) => {
    const flags = [];
    if (r.ticket_pending) flags.push("needs ticket");
    if (r.status === "Needs Scheduling" && r.reminder_count) flags.push(`${r.reminder_count} reminder${r.reminder_count > 1 ? "s" : ""}`);
    if (r.escalated_at && r.status === "Needs Scheduling") flags.push("no response");
    return `<tr data-id="${esc(r.id)}">
      <td onclick="event.stopPropagation()"><input type="checkbox" class="sel" value="${esc(r.id)}" ${state.selected.has(r.id) ? "checked" : ""} aria-label="Select ${esc(r.id)}"></td>
      <td><b>${esc(r.id)}</b></td><td>${esc(r.phase)}</td>
      <td class="trunc">${esc(r.user_name || r.last_login || "—")}</td>
      <td class="trunc">${esc(r.department)}</td><td>${esc((r.deployment_type || "").replace("Standard ", ""))}</td>
      <td><span class="pill s-${slug(r.status)}">${esc(r.status)}</span>${flags.map((f) => `<span class="flag">${esc(f)}</span>`).join("")}</td>
      <td>${esc(r.tech)}</td><td>${esc(fmtDate(r.scheduled_start))}</td><td>${esc(r.req_number)}</td>
      <td>${esc(r.old_device_returned)}</td></tr>`;
  }).join("");
  $("#rowCount").textContent = `${rows.length} of ${state.rows.length} deployments`;
  $$("#table tbody tr").forEach((tr) => tr.addEventListener("click", () => openDrawer(tr.dataset.id)));
  $$("#table .sel").forEach((c) => c.addEventListener("change", () => { c.checked ? state.selected.add(c.value) : state.selected.delete(c.value); renderBulk(); }));
  $("#selAll").checked = rows.length > 0 && rows.every((r) => state.selected.has(r.id));
  renderBulk();
}
function renderBulk() {
  $("#bulkBar").classList.toggle("hidden", state.selected.size === 0);
  $("#bulkCount").textContent = `${state.selected.size} selected`;
}
["#q", "#fPhase", "#fTech", "#fStatus"].forEach((s) => $(s).addEventListener("input", renderTable));
$("#selAll").addEventListener("change", (e) => { filtered().forEach((r) => (e.target.checked ? state.selected.add(r.id) : state.selected.delete(r.id))); renderTable(); });
$("#bulkClear").addEventListener("click", () => { state.selected.clear(); renderTable(); });
$("#bulkApply").addEventListener("click", () => guard(async () => {
  const status = $("#bulkStatus").value, ids = [...state.selected];
  if (!confirm(`Set ${ids.length} deployment(s) to "${status}"?\n\n${HINTS[status] || ""}`)) return;
  const res = await api("/api/deployments/bulk-status", { method: "POST", json: { ids, status, actor: "web" } });
  const errs = Object.entries(res.errors);
  toast(`Updated ${res.ok.length}.` + (errs.length ? ` ${errs.length} skipped: ${errs[0][1]}` : ""), errs.length > 0);
  state.selected.clear(); await loadAll();
}));

/* ===================================================================== drawer */
const HINTS = {
  "Needs Scheduling": "Emails the user the Bookings link and adds a 'create ticket' to-do (if there's no REQ# yet). Reminders go out automatically if they don't book.",
  "Scheduled": "Normally set automatically when the user books. Sends a confirmation email if a date is set.",
  "Deployed": "If the old device is already marked returned, it moves straight to Complete.",
  "Complete": "Adds a 'close ticket' to-do for the REQ#.",
};
const FIELDS = [
  ["user_name", "User"], ["netid", "NetID"], ["user_email", "User email"], ["department", "Department"],
  ["dept_contact", "Dept contact / supervisor"], ["dept_contact_email", "Dept contact email"],
  ["tech", "Tech assigned"], ["req_number", "REQ#"], ["phase", "Phase"], ["deployment_type", "Deployment type"],
  ["new_device_sn", "New device SN"], ["old_device_sn", "Old device SN"],
  ["old_device_returned", "Old device returned", ["", "Yes", "No", "N/A"]], ["location", "Location"],
  ["scheduled_start", "Scheduled start", "datetime"], ["accessories", "Accessories"],
  ["last_login", "Last login (from DubSupport)"], ["notes", "Tracker notes", "textarea"],
];

async function openDrawer(id) {
  state.current = id;
  $("#drawer").classList.remove("hidden"); $("#scrim").classList.remove("hidden");
  await renderDrawer();
}
function closeDrawer() {
  if (play.active) stopPlay();
  state.current = null;
  $("#drawer").classList.add("hidden"); $("#scrim").classList.add("hidden");
}
$("#dClose").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", () => { if ($("#askPanel").classList.contains("hidden")) closeDrawer(); else closeAsk(); });
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#askPanel").classList.contains("hidden")) closeAsk(); else closeDrawer();
});

function toLocalInput(v) {
  const d = dt(v); if (!d) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
function fromLocalInput(v) { return v ? new Date(v).toISOString() : ""; }

function lifecycleOf(d) {
  const entered = {};
  [...d.activity].reverse().forEach((a) => {
    if (a.kind === "status" && a.message.includes("→ ")) {
      const st = a.message.split("→ ")[1].split(" (")[0].trim();
      if (!entered[st]) entered[st] = a.ts;
    }
  });
  const reminders = d.activity.filter((a) => a.kind === "email" && /Reminder/i.test(a.message));
  const returned = d.activity.find((a) => a.kind === "edit" && /old device returned: .* → 'Yes'/i.test(a.message));
  const idx = ORDER[d.status] ?? -1;
  const steps = [
    { label: "Request sent", icon: ICON.send, date: entered["Needs Scheduling"] },
    { label: Math.max(d.reminder_count, reminders.length) ? `Reminders (${Math.max(d.reminder_count, reminders.length)})` : "Reminders", icon: ICON.bell, date: reminders[0] && reminders[0].ts },
    { label: "Booked", icon: ICON.cal, date: d.scheduled_start, dateLabel: d.scheduled_start ? "for " + fmtDay(d.scheduled_start) : "" },
    { label: "Deployed", icon: ICON.laptop, date: entered["Deployed"] },
    { label: "Device returned", icon: ICON.ret, date: returned && returned.ts },
    { label: "Complete", icon: ICON.check, date: entered["Complete"] },
  ];
  // state per step
  const doneUpTo = { 0: -1, 1: -1, 2: 0, 3: 2, 4: 3, 5: 5 }[idx] ?? -1; // index of last completed step
  const current = { 1: 0, 2: 1, 3: 3, 4: 4 }[idx];
  steps.forEach((s, i) => {
    s.state = i <= doneUpTo ? "done" : i === current ? "current" : "upcoming";
    if (i === 1 && idx >= 3 && !s.date) s.dateLabel = "none needed";
  });
  if (idx === 2 && d.escalated_at) steps[1].state = "alert";
  return steps;
}

function renderStepper(d) {
  const el = $("#stepper");
  if (d.status.startsWith("No Replace")) { el.innerHTML = `<div class="muted" style="grid-column:1/-1">No new device for this record (${esc(d.status)}).</div>`; return; }
  el.innerHTML = lifecycleOf(d).map((s, i) => `<div class="st ${s.state}">
    <div class="dot">${s.state === "done" ? ICON.check : s.icon}</div>
    <div class="sl">${esc(s.label)}</div>
    <div class="sd">${esc(s.dateLabel || (s.state === "done" || s.state === "alert" || i === 1 ? (s.date ? fmtDay(s.date) : "") : ""))}</div></div>`).join("");
}

function nextStepText(d) {
  const fn = firstName(d.user_name);
  switch (d.status) {
    case "Verify": case "Follow Up": return "Confirm the user and device in DubSupport, then mark it ready to send.";
    case "Replace": return `Send ${fn} the scheduling request. DeployTrack emails the Bookings link, queues the ticket and starts the reminder clock.`;
    case "Needs Scheduling": {
      const days = daysSince(d.scheduling_requested_at);
      if (d.escalated_at) return `<b>${esc(fn)} hasn't booked after ${d.reminder_count} reminders</b> (day ${days}). Their department contact was notified. Try a personal follow-up below.`;
      return `Waiting on ${esc(fn)} to book. <b>Day ${days}</b>, ${d.reminder_count ? d.reminder_count + " reminder(s) sent so far" : "first reminder goes out automatically on day " + Math.ceil(state.meta.reminder_interval_days)}.`;
    }
    case "Scheduled": return `Setup appointment <b>${esc(relDay(d.scheduled_start))} at ${esc(fmtTime(d.scheduled_start))}</b>${d.tech ? " with " + esc(d.tech) : ""}. Bring the new ${esc((d.deployment_type || "device").toLowerCase())}${d.accessories ? " and " + esc(d.accessories.toLowerCase()) : ""}.`;
    case "Deployed": return `Collect the old device${d.old_device_sn ? " (" + esc(d.old_device_sn) + ")" : ""}. Marking it returned completes the deployment and queues the ticket close.`;
    case "Complete": return d.close_ticket_pending ? `All done. Close <b>${esc(d.req_number)}</b> in the Service Dashboard.` : "All done. 🎉 Device deployed, old one returned, ticket closed.";
    default: return esc(d.status);
  }
}

async function renderDrawer() {
  const d = await api(`/api/deployments/${encodeURIComponent(state.current)}`);
  state.currentData = d;
  $("#dAvatar").textContent = initials(d.user_name);
  $("#dTitle").textContent = d.user_name || d.last_login || "Shared device";
  $("#dSub").innerHTML = `${esc(d.id)} · ${esc(d.department)} · ${esc(d.deployment_type)}${d.phase !== "" ? " · Phase " + esc(d.phase) : ""} &nbsp;<span class="pill s-${slug(d.status)}">${esc(d.status)}</span>`;
  renderStepper(d);
  $("#dNext").innerHTML = nextStepText(d);

  const sel = $("#dStatusSel");
  sel.innerHTML = state.meta.statuses.map((s) => `<option ${s === d.status ? "selected" : ""}>${esc(s)}</option>`).join("");
  sel.onchange = () => { $("#dHint").textContent = sel.value !== d.status ? HINTS[sel.value] || "" : ""; };
  $("#dHint").textContent = "";
  $("#dStatusApply").onclick = () => setStatus(d.id, sel.value);

  renderActions(d); renderTicket(d); renderAI(d);

  $("#dForm").innerHTML = FIELDS.map(([k, label, kind]) => {
    const v = d[k] ?? "";
    const wide = kind === "textarea" || k === "accessories" ? "wide" : "";
    let input;
    if (Array.isArray(kind)) input = `<select name="${k}">${kind.map((o) => `<option ${o === v ? "selected" : ""} value="${esc(o)}">${esc(o || "—")}</option>`).join("")}${kind.includes(v) ? "" : `<option selected>${esc(v)}</option>`}</select>`;
    else if (kind === "textarea") input = `<textarea name="${k}" rows="2">${esc(v)}</textarea>`;
    else if (kind === "datetime") input = `<input type="datetime-local" name="${k}" value="${toLocalInput(v)}">`;
    else input = `<input name="${k}" value="${esc(v)}">`;
    return `<label class="${wide}">${esc(label)}${input}</label>`;
  }).join("");
  $("#dSave").onclick = () => saveForm(d);
  $("#dActivity").innerHTML = d.activity.map((a) => feedItem(a, false)).join("") || '<li class="empty">No history yet.</li>';
  $("#dEmailCount").textContent = d.emails.length;
  $("#dEmails").innerHTML = d.emails.map(mailCard).join("") || '<p class="muted">No emails yet.</p>';
  return d;
}

function renderActions(d) {
  const a = [];
  const btn = (label, fn, cls = "") => a.push([label, fn, cls]);
  if (["Verify", "Follow Up"].includes(d.status)) btn("Mark ready to send", () => setStatus(d.id, "Replace"), "primary");
  if (d.status === "Replace") btn("Send scheduling request", () => setStatus(d.id, "Needs Scheduling"), "primary");
  if (d.status === "Needs Scheduling") {
    if (state.meta.demo_mode) btn("Simulate a booking (demo)", () => simulateBooking(d), "primary");
    btn("Booked by phone / in person", () => manualBooking(d));
  }
  if (d.status === "Scheduled") {
    btn("Mark deployed", () => setStatus(d.id, "Deployed"), "primary");
    if (state.meta.demo_mode) btn("Simulate cancellation (demo)", () => guard(async () => {
      await api("/api/bookings/webhook", { method: "POST", json: { event: "cancelled", deployment_id: d.id } });
      toast("Booking cancelled → back to waiting, rebook email sent."); await refresh();
    }));
  }
  if (d.status === "Deployed") btn("Old device returned", () => patch(d.id, { old_device_returned: "Yes" }, "Old device returned → Complete."), "primary");
  if (d.close_ticket_pending) btn(`Ticket ${d.req_number} closed`, () => guard(async () => {
    await api(`/api/deployments/${d.id}/ticket-closed?actor=web`, { method: "POST" }); toast("Marked ticket closed."); await refresh();
  }));
  const box = $("#dActions"); box.innerHTML = "";
  a.forEach(([label, fn, cls]) => { const b = document.createElement("button"); b.textContent = label; b.className = cls; b.onclick = fn; box.appendChild(b); });
}

function ticketText(d) {
  return `Short description: Device refresh deployment ${d.id} — ${d.user_name || d.netid}\nRequested for: ${d.netid}\nAssigned to: ${d.tech || "(unassigned)"}\n\nComputer refresh deployment for ${d.user_name} (${d.netid}).\nDepartment: ${d.department}\nDeployment type: ${d.deployment_type}\nNew device SN: ${d.new_device_sn || "TBD"}\nOld device SN: ${d.old_device_sn || "TBD"}\nAccessories: ${d.accessories}\nLocation: ${d.location}\nTracker ID: ${d.id}`;
}
function renderTicket(d) {
  const box = $("#dTicket");
  if (!d.ticket_pending) { box.classList.add("hidden"); return; }
  const t = ticketText(d);
  box.classList.remove("hidden");
  box.innerHTML = `<h4>🎫 Ticket needed in the Service Dashboard</h4>
    <p class="muted small" style="margin:0">Copy this into a new ticket, then paste the REQ# below.</p>
    <pre>${esc(t)}</pre>
    <div class="row"><button id="copyTicket">Copy text</button>
    <input id="reqIn" placeholder="REQ0…" style="flex:1;min-width:120px"><button id="reqSave" class="primary">Save REQ#</button></div>`;
  $("#copyTicket").onclick = () => navigator.clipboard.writeText(t).then(() => toast("Copied."), () => toast("Copy failed — select the text instead.", true));
  $("#reqSave").onclick = () => { const v = $("#reqIn").value.trim(); if (v) patch(d.id, { req_number: v }, "REQ# saved."); };
}

async function refresh() { await loadAll(); if (state.current) await renderDrawer(); }
function setStatus(id, status) {
  return guard(async () => {
    await api(`/api/deployments/${encodeURIComponent(id)}/status`, { method: "POST", json: { status, actor: "web" } });
    toast(`${id} → ${status}`); await refresh();
  });
}
function patch(id, fields, msg = "Saved.") {
  return guard(async () => {
    await api(`/api/deployments/${encodeURIComponent(id)}`, { method: "PATCH", json: Object.assign({ actor: "web" }, fields) });
    toast(msg); await refresh();
  });
}
function saveForm(d) {
  const f = {};
  $$("#dForm [name]").forEach((el) => {
    let v = el.value;
    if (el.type === "datetime-local") { if (toLocalInput(d[el.name]) === el.value) return; v = fromLocalInput(v); }
    if (v !== (d[el.name] ?? "")) f[el.name] = v;
  });
  if (!Object.keys(f).length) return toast("Nothing changed.");
  patch(d.id, f, `Saved ${Object.keys(f).length} change(s).`);
}
function nextWeekdayAt(hour) {
  const d = new Date(); d.setDate(d.getDate() + 2);
  while ([0, 6].includes(d.getDay())) d.setDate(d.getDate() + 1);
  d.setHours(hour, 0, 0, 0); return d;
}
function simulateBooking(d) {
  return guard(async () => {
    const start = nextWeekdayAt(10), end = new Date(start.getTime() + 3600e3);
    const res = await api("/api/bookings/webhook", { method: "POST", json: { event: "created", customer_email: d.user_email, start: start.toISOString(), end: end.toISOString() } });
    toast(res.matched.length ? `Booking received → ${res.matched.join(", ")} Scheduled.` : "No deployment matched that email.", !res.matched.length);
    await refresh();
  });
}
function manualBooking(d) {
  const v = prompt("Appointment date and time (e.g. 2026-12-03 10:30):");
  if (!v) return;
  const when = new Date(v.replace(" ", "T"));
  if (isNaN(when)) return toast("Couldn't read that date.", true);
  return guard(async () => {
    await api("/api/bookings/webhook", { method: "POST", json: { event: "created", deployment_id: d.id, start: when.toISOString(), end: new Date(when.getTime() + 3600e3).toISOString() } });
    toast("Scheduled."); await refresh();
  });
}
$("#noteForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("#noteText").value.trim(); if (!text) return;
  guard(async () => {
    await api(`/api/deployments/${encodeURIComponent(state.current)}/notes`, { method: "POST", json: { text, actor: "web" } });
    $("#noteText").value = ""; await renderDrawer();
  });
});
function mailCard(m) {
  return `<div class="mail"><div class="h"><span class="subj">${esc(m.subject)}</span><span class="muted small">${esc(fmtAgo(m.ts))}</span></div>
    <div class="muted small">To: ${esc(m.to_addr)}${m.cc ? " · Cc: " + esc(m.cc) : ""} · ${m.delivered ? "sent" : "outbox only"}</div>
    ${m.error ? `<div class="err">Delivery failed: ${esc(m.error)}</div>` : ""}
    <details><summary>Show email</summary><pre>${esc(m.body)}</pre></details></div>`;
}

/* ===================================================================== AI assist */
const aiSrc = (p) => `<span class="src">${p === "offline" ? "Offline rules (no AI connected)" : "Written by " + esc(state.meta.ai.name) + " · review before sending"}</span>`;

function renderAI(d) {
  const relevant = ["Needs Scheduling", "Scheduled", "Replace", "Follow Up"].includes(d.status) && d.user_email;
  $("#aiBox").classList.toggle("hidden", !relevant);
  $("#aiFollowup").classList.toggle("hidden", d.status !== "Needs Scheduling");
  $("#aiDraft").classList.add("hidden"); $("#aiSuggestion").classList.add("hidden");
  if (!play.active) $("#replyText").value = "";
  $("#aiFollowup").onclick = () => draftFollowup(d);
  $("#aiInterpret").onclick = () => guard(() => interpretReply(d));
}
async function busy(btn, label, fn) {
  const old = btn.textContent; btn.disabled = true; btn.textContent = label;
  try { return await fn(); } finally { btn.disabled = false; btn.textContent = old; }
}
function draftFollowup(d) {
  const card = $("#aiDraft");
  return guard(() => busy($("#aiFollowup"), "Drafting…", async () => {
    const r = await api(`/api/ai/followup/${encodeURIComponent(d.id)}`, { method: "POST" });
    card.classList.remove("hidden");
    card.innerHTML = `<label>Subject<input id="fuSubj" value="${esc(r.subject)}"></label>
      <label>Email to ${esc(d.user_email)}<textarea id="fuBody" rows="9">${esc(r.body)}</textarea></label>
      ${aiSrc(r.provider)}
      <div class="row"><button id="fuSend" class="primary">Send</button><button id="fuDiscard" class="ghost">Discard</button></div>`;
    $("#fuDiscard").onclick = () => card.classList.add("hidden");
    $("#fuSend").onclick = () => guard(async () => {
      await api(`/api/deployments/${encodeURIComponent(d.id)}/email`, { method: "POST", json: { subject: $("#fuSubj").value, body: $("#fuBody").value, actor: "web" } });
      toast("Follow-up sent."); await refresh();
    });
  }));
}
async function interpretReply(d) {
  const text = $("#replyText").value.trim();
  if (!text) throw new Error("Paste the user's reply first.");
  const card = $("#aiSuggestion");
  return busy($("#aiInterpret"), "Reading…", async () => {
    const r = await api(`/api/ai/reply/${encodeURIComponent(d.id)}`, { method: "POST", json: { text } });
    const opts = ['<option value="">No status change</option>'].concat(state.meta.statuses.map((s) => `<option ${s === r.suggested_status ? "selected" : ""}>${esc(s)}</option>`));
    card.classList.remove("hidden");
    card.innerHTML = `<div class="summary-line">✦ ${esc(r.summary || "")}</div>
      ${r.needs_human ? `<div class="caution">Check this one: ${esc(r.reason || "")}</div>` : ""}
      <label>Set status<select id="sgStatus">${opts.join("")}</select></label>
      <label>Appointment<input type="datetime-local" id="sgStart" value="${toLocalInput(r.scheduled_start)}"></label>
      <label>Note to log<input id="sgNote" value="${esc(r.note || "")}"></label>
      <label>Reply subject<input id="sgSubj" value="${esc(r.reply_subject || "")}"></label>
      <label>Reply to ${esc(d.user_email)}<textarea id="sgBody" rows="6">${esc(r.reply_body || "")}</textarea></label>
      <label class="check" style="flex-direction:row;color:var(--text)"><input type="checkbox" id="sgSend" checked> Send this reply</label>
      ${aiSrc(r.provider)}
      <div class="row"><button id="sgApply" class="primary">Approve &amp; apply</button><button id="sgDiscard" class="ghost">Discard</button></div>`;
    $("#sgDiscard").onclick = () => card.classList.add("hidden");
    $("#sgApply").onclick = () => guard(() => applySuggestion(d, text));
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return r;
  });
}
async function applySuggestion(d, text) {
  const body = {
    reply_text: text, status: $("#sgStatus").value || null, scheduled_start: fromLocalInput($("#sgStart").value) || null,
    note: $("#sgNote").value, reply_subject: $("#sgSubj").value, reply_body: $("#sgBody").value, send_reply: $("#sgSend").checked, actor: "web",
  };
  if (body.status === "Scheduled" && !body.scheduled_start) throw new Error("Add an appointment time to schedule.");
  await api(`/api/ai/reply/${encodeURIComponent(d.id)}/apply`, { method: "POST", json: body });
  toast("Done — record updated" + (body.send_reply ? " and reply sent." : "."));
  await refresh();
}

/* ===================================================================== Ask panel */
const CHIPS = ["Who in Phase 2 hasn't booked yet?", "What's on the schedule today and tomorrow?", "Who isn't responding to reminders?",
  "Which old devices still need collecting?", "How is Phase 2 going?", "What tickets still need to be created?"];
const askHistory = [];
function openAsk() {
  $("#askPanel").classList.remove("hidden"); $("#scrim").classList.remove("hidden");
  const chips = $("#askChips");
  if (!chips.childElementCount) CHIPS.forEach((c) => {
    const b = document.createElement("button"); b.type = "button"; b.textContent = c; b.onclick = () => ask(c); chips.appendChild(b);
  });
  $("#askInput").focus();
}
function closeAsk() {
  $("#askPanel").classList.add("hidden");
  if ($("#drawer").classList.contains("hidden")) $("#scrim").classList.add("hidden");
}
$("#openAsk").addEventListener("click", openAsk);
$("#askClose").addEventListener("click", closeAsk);
const linkIds = (text) => esc(text).replace(/\b(D\d{3})\b/g, '<span class="idlink" data-id="$1">$1</span>');
function addBubble(cls, html) {
  const el = document.createElement("div"); el.className = "bubble " + cls; el.innerHTML = html;
  $("#askLog").appendChild(el); el.scrollIntoView({ block: "end", behavior: "smooth" }); return el;
}
async function ask(q) {
  q = (q || "").trim(); if (!q) return;
  $("#askInput").value = "";
  addBubble("user", esc(q));
  const pending = addBubble("bot thinking", "Thinking…");
  try {
    const r = await api("/api/ai/ask", { method: "POST", json: { question: q, history: askHistory } });
    pending.className = "bubble bot";
    pending.innerHTML = linkIds(r.answer) + `<span class="src">${r.provider === "offline" ? "Offline rules" : esc(state.meta.ai.name)}</span>`;
    $$(".idlink", pending).forEach((a) => a.addEventListener("click", () => { closeAsk(); openDrawer(a.dataset.id); }));
    askHistory.push({ role: "user", content: q }, { role: "assistant", content: r.answer });
  } catch (e) { pending.className = "bubble bot err"; pending.textContent = e.message; }
}
$("#askForm").addEventListener("submit", (e) => { e.preventDefault(); ask($("#askInput").value); });

/* ===================================================================== Play lifecycle */
const play = { active: false, paused: false, i: 0, steps: [], timer: null, dep: null, running: false };

function nextTuesday10() {
  const d = new Date(); d.setDate(d.getDate() + 1);
  while (d.getDay() !== 2) d.setDate(d.getDate() + 1);
  return d;
}

function buildSteps(dep) {
  const fn = firstName(dep.user_name), ai = state.meta.ai.live ? state.meta.ai.name : "The assistant";
  const reply = `Hi! Sorry for the slow reply, I was out last week. Could we do Tuesday at 10am? I'm on site that day.`;
  return [
    { title: `Meet ${fn}`, text: `${dep.user_name} (${dep.department}) has a new ${(dep.deployment_type || "device").toLowerCase()} waiting. Today this is a spreadsheet row; here it's a tracked lifecycle.` },
    { title: "Send the scheduling request", text: `One click. DeployTrack emails ${fn} the Bookings link, adds a ticket to-do and starts the reminder clock.`,
      run: async () => { await api(`/api/deployments/${dep.id}/status`, { method: "POST", json: { status: "Needs Scheduling", actor: "demo" } }); } },
    { title: "Ticket created", text: "The tech creates the ticket from the prefilled text and pastes the REQ#. The to-do disappears.",
      run: async () => { await api(`/api/deployments/${dep.id}`, { method: "PATCH", json: { req_number: "REQ09" + String(Math.floor(1e6 + Math.random() * 9e6)).slice(0, 7), actor: "demo" } }); } },
    { title: `${fn} replies by email`, text: `Instead of booking, ${fn} writes back: “${reply}”. ${ai} reads it…`,
      noRefresh: true,
      run: async (d) => { $("#replyText").value = reply; $("#aiBox").scrollIntoView({ behavior: "smooth", block: "center" }); await sleep(600); await interpretReply(d); } },
    { title: "Approve the suggestion", text: `${ai} suggested the status, appointment time, a note and a reply. One click to approve: ${fn} is Scheduled and gets a confirmation.`,
      run: async (d) => {
        if ($("#sgStart") && !$("#sgStart").value) { const t = nextTuesday10(); t.setHours(10, 0, 0, 0); $("#sgStart").value = toLocalInput(t.toISOString()); $("#sgStatus").value = "Scheduled"; }
        await applySuggestion(d, $("#replyText").value || reply); $("#stepper").scrollIntoView({ behavior: "smooth", block: "center" });
      } },
    { title: "Appointment day", text: "The tech sets up the new device and marks it deployed. The old device still needs to come back.",
      run: async () => { await api(`/api/deployments/${dep.id}/status`, { method: "POST", json: { status: "Deployed", actor: "demo" } }); } },
    { title: "Old device returned", text: "Marking the old device returned completes the deployment automatically and queues the ticket close.",
      run: async () => { await api(`/api/deployments/${dep.id}`, { method: "PATCH", json: { old_device_returned: "Yes", actor: "demo" } }); } },
    { title: "Complete ✓", text: "", run: async (d) => {
      const n = d.emails.length;
      $("#pbText").textContent = `From request to complete: ${n} emails sent for you, every step logged, nothing typed into a spreadsheet.`;
    } },
  ];
}

async function startPlay() {
  let dep = null;
  try {   // the server adds a fresh fictional person, so shared demos never run out
    const { id } = await api("/api/demo/play-record", { method: "POST" });
    await loadAll(); dep = state.rows.find((r) => r.id === id);
  } catch { dep = state.rows.find((r) => r.status === "Replace" && r.user_email); }
  if (!dep) return toast("No 'Ready to send' record with an email left. Reset the demo first.", true);
  closeAsk();
  Object.assign(play, { active: true, paused: false, i: 0, dep, steps: buildSteps(dep) });
  $("#playBanner").classList.remove("hidden");
  $("#pbDots").innerHTML = play.steps.map(() => "<i></i>").join("");
  $("#pbPause").textContent = "Pause";
  await openDrawer(dep.id);
  runStep(0);
}
async function runStep(i) {
  clearTimeout(play.timer);
  if (!play.active || i >= play.steps.length) return;
  play.i = i; play.running = true;
  const s = play.steps[i];
  $("#pbStep").textContent = `Step ${i + 1} of ${play.steps.length}`;
  $("#pbTitle").textContent = s.title; $("#pbText").textContent = s.text;
  $$("#pbDots i").forEach((d, k) => d.classList.toggle("on", k <= i));
  try {
    if (s.run) {
      if (i === play.steps.length - 1) await refresh();
      await s.run(state.currentData);
      if (!play.active) return;
      if (!s.noRefresh && i !== play.steps.length - 1) await refresh();
    }
  } catch (e) { toast(e.message, true); }
  play.running = false;
  if (i === play.steps.length - 1) { $("#pbPause").textContent = "Replay"; play.paused = true; $("#pbNext").disabled = true; return; }
  $("#pbNext").disabled = false;
  if (!play.paused) play.timer = setTimeout(() => runStep(i + 1), i === 3 ? 5200 : 4200);
}
function stopPlay() {
  clearTimeout(play.timer); play.active = false; $("#playBanner").classList.add("hidden"); $("#pbNext").disabled = false;
}
$("#playBtn").addEventListener("click", startPlay);
$("#pbStop").addEventListener("click", stopPlay);
$("#pbNext").addEventListener("click", () => { if (!play.running) runStep(play.i + 1); });
$("#pbPause").addEventListener("click", () => {
  if ($("#pbPause").textContent === "Replay") { stopPlay(); startPlay(); return; }
  play.paused = !play.paused; $("#pbPause").textContent = play.paused ? "Resume" : "Pause";
  if (play.paused) clearTimeout(play.timer); else if (!play.running) runStep(play.i + 1);
});

/* ===================================================================== to-do, outbox, activity */
const TODO_GROUPS = {
  upcoming: "📅 Today & tomorrow", create_ticket: "🎫 Create ticket", unresponsive: "🔔 Not responding",
  collect_device: "↩ Old devices to collect", close_ticket: "✓ Close ticket", missing_email: "✉ Missing email",
};
async function refreshTodoCount() {
  const items = await api("/api/todo");
  $("#todoCount").textContent = items.filter((i) => i.kind !== "collect_device").length || "";
  return items;
}
async function loadTodo() {
  const items = await refreshTodoCount();
  $("#todoList").innerHTML = Object.entries(TODO_GROUPS).map(([k, title]) => {
    const g = items.filter((i) => i.kind === k);
    if (!g.length) return "";
    return `<div class="card todo-group"><h3><span>${esc(title)}</span><span class="tag">${g.length}</span></h3>${g.map((i) =>
      `<div class="todo-item" data-id="${esc(i.deployment_id)}"><b>${esc(i.deployment_id)}</b> — ${esc(i.title)}</div>`).join("")}</div>`;
  }).join("") || '<div class="card empty">Nothing to do. 🎉</div>';
  $$("#todoList .todo-item").forEach((el) => el.addEventListener("click", () => openDrawer(el.dataset.id)));
}
async function loadOutbox() {
  const mails = await api("/api/outbox");
  $("#outboxList").innerHTML = mails.map(mailCard).join("") || '<p class="muted">No emails yet.</p>';
}
async function loadActivity() {
  const acts = await api("/api/activity?limit=200");
  $("#activityList").innerHTML = acts.map((a) => feedItem(a, true)).join("");
  bindDeps($("#activityList"));
}

/* ===================================================================== settings */
$("#importForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const file = $("#importFile").files[0]; if (!file) return;
  guard(async () => {
    const fd = new FormData(); fd.append("file", file);
    const r = await api(`/api/import?overwrite=${$("#importOverwrite").checked}`, { method: "POST", body: fd });
    $("#importResult").textContent = `Created ${r.created}, updated ${r.updated}, skipped ${r.skipped}.`;
    await loadAll();
  });
});
$("#exportLink").addEventListener("click", (e) => {
  if (!getKey()) return;
  e.preventDefault();
  guard(async () => {
    const csv = await api("/api/export.csv");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = "deploytrack-export.csv"; a.click();
  });
});
$("#runReminders").addEventListener("click", () => guard(async () => {
  const days = Number($("#daysAhead").value || 0);
  const r = await api(`/api/reminders/run?days_ahead=${days}`, { method: "POST" });
  $("#reminderResult").textContent = `Reminded: ${r.reminded.join(", ") || "none"} · Escalated: ${r.escalated.join(", ") || "none"} · Need manual follow-up: ${r.needs_contact_email.join(", ") || "none"}`;
  await loadAll();
}));
$("#resetDemo").addEventListener("click", () => {
  if (!confirm("Reset the demo? This wipes the fake demo data and generates a fresh set.")) return;
  guard(async () => {
    const r = await api("/api/demo/reset", { method: "POST" });
    closeDrawer(); state.selected.clear(); state.lastFeedId = 0; await loadAll();
    toast(`Fresh demo data: ${r.deployments} deployments.`);
  });
});

/* ===================================================================== boot */
(async function boot() {
  await guard(async () => {
    state.meta = await api("/api/meta");
    const m = state.meta, ai = m.ai, b = [];
    if (m.demo_mode) b.push('<span class="badge">Demo data</span>');
    b.push(m.email_mode === "smtp" ? (m.redirect_all_email_to ? '<span class="badge warn">Email → test inbox</span>' : '<span class="badge live">Email live</span>') : '<span class="badge">Email: outbox only</span>');
    b.push(ai.live ? `<span class="badge live" title="${esc(ai.model || "")}">AI: ${esc(ai.name)}${ai.ready ? "" : " (not configured)"}</span>` : '<span class="badge warn" title="No AI service connected — using built-in rules">AI: offline rules</span>');
    $("#modeBadges").innerHTML = b.join("");
    $$(".ai-name").forEach((el) => (el.textContent = ai.live ? ai.name : "AI"));
    $("#resetDemo").classList.toggle("hidden", !m.demo_db);
    $("#askSub").textContent = ai.live ? `Answers from ${ai.name} using live deployment data.` : "Offline mode: built-in rules, not AI. Connect Purple or Claude in .env.";
    $("#aiInfo").textContent = ai.live ? `Connected to ${ai.name}${ai.model ? " (" + ai.model + ")" : ""}.${ai.ready ? "" : " Missing credentials — see .env.example."}` : "Not connected. AI features use built-in rules. Set AI_PROVIDER in .env (purple via 'openai', or 'claude').";
    $("#bulkStatus").innerHTML = m.statuses.map((s) => `<option>${esc(s)}</option>`).join("");
    $("#outboxNote").textContent = m.email_mode === "smtp" ? "Every email the app has sent." : "Emails are saved here but not actually sent (EMAIL_MODE=outbox).";
    $("#reminderInfo").textContent = `Users who haven't booked get a reminder every ${m.reminder_interval_days} day(s), up to ${m.max_reminders}; then their department contact is emailed. Runs automatically.`;
    await loadAll();
    // deep links: #tab=board  #d=D255  #play
    const h = new URLSearchParams(location.hash.slice(1));
    if (h.get("tab")) showTab(h.get("tab"));
    if (h.get("d")) await openDrawer(h.get("d"));
    if (h.has("play")) startPlay();
  });
})();
