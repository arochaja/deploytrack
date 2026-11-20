"""Build the single-page web demo (web/dist/deploytrack.html) from the app's UI + web/backend.js.

Run:  python3 web/build.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
OUT = ROOT / "web" / "dist" / "deploytrack.html"


def must_replace(src, old, new, count=1):
    assert old in src, f"pattern not found: {old[:70]!r}"
    return src.replace(old, new, count)


def slice_replace(src, start, end, new):
    i = src.index(start)
    j = src.index(end, i)
    return src[:i] + new + src[j:]


# ------------------------------------------------------------------ CSS
css = (STATIC / "style.css").read_text()
m = re.search(r"@media \(prefers-color-scheme: dark\) \{\n  :root \{(.*?)\n  \}\n\}", css, re.S)
dark = m.group(1)
css = css.replace(m.group(0),
                  "@media (prefers-color-scheme: dark) {\n  :root:not([data-theme=\"light\"]) {" + dark +
                  "\n    color-scheme: dark;\n  }\n}\n:root[data-theme=\"dark\"] {" + dark + "\n  color-scheme: dark;\n}")
css = must_replace(css, "  --hero-grad: linear-gradient(120deg, #32195f 0%, #4b2e83 55%, #6a49ad 100%);\n}",
                   "  --hero-grad: linear-gradient(120deg, #32195f 0%, #4b2e83 55%, #6a49ad 100%);\n  --note-soft: #f6efdc;\n}")
css = css.replace("--hero-grad: linear-gradient(120deg, #1d1236 0%, #2f1d58 55%, #432c7a 100%);",
                  "--hero-grad: linear-gradient(120deg, #1d1236 0%, #2f1d58 55%, #432c7a 100%); --note-soft: #3a3220;")
css = must_replace(css, ".feed .k-note .ic { background: #f6efdc; }", ".feed .k-note .ic { background: var(--note-soft); }")
css = must_replace(css, ".hero-bar { background: var(--hero-grad); color: #fff; position: sticky; top: 0;",
                   "body::before { content: \"\"; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 10; background: var(--uw-deep); }\n"
                   ".hero-bar { background: var(--hero-grad); color: #fff; position: sticky; top: env(safe-area-inset-top, 0px);")
css = must_replace(css, ".hero-inner { max-width: 1400px; margin: 0 auto; padding: 14px 24px 8px;",
                   ".hero-inner { max-width: 1400px; margin: 0 auto; padding-block: 14px 8px; padding-left: max(24px, env(safe-area-inset-left, 0px)); padding-right: max(24px, env(safe-area-inset-right, 0px));")
css = must_replace(css, "main { padding: 22px 24px 60px;",
                   "main { padding-block: 22px 60px; padding-left: max(24px, env(safe-area-inset-left, 0px)); padding-right: max(24px, env(safe-area-inset-right, 0px));")
css = must_replace(css, "  main { padding: 16px 16px 40px; }\n  .hero-inner { padding: 12px 16px 6px; }",
                   "  main { padding-block: 16px 40px; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }\n"
                   "  .hero-inner { padding-block: 12px 6px; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }")
css += """
/* artifact frame: keep fixed panels clear of phone system bars */
.drawer { padding-top: env(safe-area-inset-top, 0px); padding-bottom: env(safe-area-inset-bottom, 0px); padding-right: env(safe-area-inset-right, 0px); }
.toast { bottom: calc(20px + env(safe-area-inset-bottom, 0px)); }
.demo-note { display: inline-flex; gap: 6px; align-items: center; font-size: 12px; color: var(--muted); background: var(--surface-2); border: 1px solid var(--border); border-radius: 99px; padding: 3px 10px; margin-top: 8px; }
.about li { margin-bottom: 6px; }
"""

# ------------------------------------------------------------------ HTML body
html = (STATIC / "index.html").read_text()
body = html[html.index("<header"):html.index('<script src="/static/app.js"></script>')]
body = must_replace(body, '<p class="muted">Every device moves left to right. ⚡ marks the steps DeployTrack does for you.</p>',
                    '<p class="muted">Every device moves left to right. ⚡ marks the steps DeployTrack does for you.</p>\n'
                    '        <span class="demo-note">Demo with fictional people · your changes stay in your browser · nothing is emailed</span>')
settings = '''<section id="tab-data" class="tab">
    <div class="cards">
      <div class="card about">
        <h3>About this demo</h3>
        <p class="muted">A working preview of DeployTrack, the deployment tracker for the 2026 UWF Computer Devices Refresh.</p>
        <ul class="muted small">
          <li>All 235 people are fictional. Emails go to the Outbox tab and are never sent.</li>
          <li>Everything you change stays in your own browser. Reset any time from the header.</li>
          <li>The real app also imports the Excel tracker, sends email and runs reminders on its own.</li>
        </ul>
      </div>
      <div class="card">
        <h3>Reminders</h3>
        <p class="muted" id="reminderInfo"></p>
        <div class="row">
          <label for="daysAhead">Pretend</label><input type="number" id="daysAhead" value="4" min="0" step="1" style="width:4.5em"><span>days passed</span>
          <button id="runReminders" class="primary">Run reminders now</button>
        </div>
        <p id="reminderResult" class="muted"></p>
      </div>
      <div class="card">
        <h3>AI</h3>
        <p class="muted" id="aiInfo"></p>
      </div>
    </div>
  </section>'''
body = slice_replace(body, '<section id="tab-data" class="tab">', "</main>", settings + "\n")

# ------------------------------------------------------------------ JS
js = (STATIC / "app.js").read_text()
js = slice_replace(js, "function getKey()", "function toast(",
                   "async function api(path, opts = {}) {\n  const r = await Backend.request(path, opts);\n"
                   "  if (state.meta && state.meta.ai && state.meta.ai.live && Backend.isAiDeclined()) renderBadges();\n  return r;\n}\n\n")
js = must_replace(js, '    const r = await api("/api/ai/ask", { method: "POST", json: { question: q, history: askHistory } });',
                  '    const r = await Backend.ask(q, askHistory, (text) => { pending.className = "bubble bot"; pending.innerHTML = linkIds(text); });\n'
                  '    if (Backend.isAiDeclined()) renderBadges();')
js = slice_replace(js, "function manualBooking(d) {", "$(\"#noteForm\")", '''function manualBooking(d) {
  if ($("#mbWhen")) return $("#mbWhen").focus();
  const wrap = document.createElement("div"); wrap.className = "row"; wrap.style.width = "100%";
  wrap.innerHTML = `<input type="datetime-local" id="mbWhen" aria-label="Appointment time" value="${toLocalInput(nextWeekdayAt(10).toISOString())}"><button class="primary" id="mbSave">Save booking</button>`;
  $("#dActions").appendChild(wrap);
  $("#mbSave").onclick = () => {
    const when = new Date($("#mbWhen").value);
    if (isNaN(when)) return toast("Pick a date and time.", true);
    guard(async () => {
      await api("/api/bookings/webhook", { method: "POST", json: { event: "created", deployment_id: d.id, start: when.toISOString(), end: new Date(when.getTime() + 3600e3).toISOString() } });
      toast("Scheduled."); await refresh();
    });
  };
}
''')
js = must_replace(js, '  if (!confirm(`Set ${ids.length} deployment(s) to "${status}"?\\n\\n${HINTS[status] || ""}`)) return;\n', "")
js = must_replace(js, '  if (!confirm("Reset the demo? This wipes the fake demo data and generates a fresh set.")) return;\n',
                  '''  const btn = $("#resetDemo");
  if (!btn.dataset.armed) {
    btn.dataset.armed = "1"; btn.textContent = "Click again to reset";
    setTimeout(() => { delete btn.dataset.armed; btn.textContent = "↺ Reset demo"; }, 3500);
    return;
  }
  delete btn.dataset.armed; btn.textContent = "↺ Reset demo";
''')
js = slice_replace(js, '$("#importForm").addEventListener', '$("#runReminders")', "")
js = slice_replace(js, "    const m = state.meta, ai = m.ai, b = [];", '    $("#bulkStatus")', "    renderBadges();\n    const m = state.meta;\n")
js = slice_replace(js, "    // deep links:", "  });\n})();", '''    // links: #board  #todo  #play  #D257
    const route = () => {
      const h = location.hash.slice(1);
      if (!h) return;
      if (h === "play") startPlay();
      else if (/^D\\d+$/.test(h)) openDrawer(h);
      else if ($("#tab-" + h)) showTab(h);
    };
    route();
    addEventListener("hashchange", route);
''')
js = must_replace(js, "(async function boot() {", '''function renderBadges() {
  const m = state.meta; m.ai = Backend.aiStatus(); const ai = m.ai;
  $("#modeBadges").innerHTML = '<span class="badge">Demo data</span><span class="badge">Email: outbox only</span>' +
    (ai.live ? '<span class="badge live">AI: Claude</span>' : '<span class="badge warn" title="Claude isn\\'t available in this view, so AI features use built-in rules">AI: offline rules</span>');
  $$(".ai-name").forEach((el) => (el.textContent = ai.live ? ai.name : "AI"));
  $("#resetDemo").classList.remove("hidden");
  $("#askSub").textContent = ai.live ? "Answers come from Claude, using this demo's data. The first question asks your permission." : "Claude isn't available here, so answers come from built-in rules.";
  $("#aiInfo").textContent = ai.live
    ? "AI features use Claude on your own Claude account. The first AI action asks your permission. If you decline, they switch to built-in rules."
    : "Claude isn't available in this view (or permission was declined), so AI features use built-in rules. The real app connects to Purple or Claude.";
}

(async function boot() {
  Backend.init();''')
js += '''
// Light up real AI once the viewer's Claude app answers. Until then (or without it) the offline rules run.
if (window.claude && window.claude.use) {
  window.claude.use("sample").then((fn) => { if (fn) { Backend.setSample(fn); if (state.meta) renderBadges(); } }).catch(() => {});
}
'''
for bad in ("prompt(", "confirm(", "/static/", "localStorage.getItem(\"dt-key"):
    assert bad not in js, f"leftover {bad} in js"

backend = (ROOT / "web" / "backend.js").read_text()
page = f'''<title>DeployTrack</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">
<style>
{css}
</style>
{body}
<script>
{backend}
</script>
<script>
{js}
</script>
'''
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(page)
print(f"wrote {OUT} ({len(page) // 1024} KB)")
