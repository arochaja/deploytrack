# DeployTrack

One place to run the 2026 UWF Computer Devices Refresh. It replaces jumping between the Excel
tracker, the Service Dashboard, Bookings and DubSupport.

```
Verify → Follow Up → Replace → Needs Scheduling → Scheduled → Deployed → Complete
                                  │                  ▲          │            │
                 emails Bookings link,      booking webhook   old device   "close ticket"
                 "create ticket" to-do,     (or demo button)  returned →   to-do
                 reminders every N days,                       Complete
                 then escalates to dept contact
```

## Run it

```sh
./run.sh            # http://127.0.0.1:8000  (API docs at /docs)
```

Tabs: **Overview** (the lifecycle at a glance, KPIs, charts), **Board** (every deployment by
stage), **All deployments** (table), **To-Do**, **Outbox**, **Activity** and **Settings**.
**▶ Play lifecycle** walks one fake record from request to complete in about 35 seconds,
for presentations. Deep links: `/#tab=board`, `/#d=D257`, `/#play`.

By default it runs in **demo mode** on fake data (`data/demo.db`): 235 made-up staff with
`@example.edu` emails, dates relative to today, and a **↺ Reset demo** button. To use your real
tracker, set `DEMO_MODE=false` in `.env` (data in `data/deploytrack.db`, never reset).

On the **Import / Settings** tab, upload the tracker. In Excel, use *File → Save As → CSV*.
The column names already match the current sheet. Rows without an ID are skipped. A user's email
defaults to `NETID@uw.edu`. Shared `UWFTECH` devices get no email.

Run the tests with `.venv/bin/python tests/test_flow.py && .venv/bin/python tests/test_ai_demo.py`.

## Put it on the web (Render)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/arochaja/deploytrack)

`render.yaml` sets up one free web service running the full app in **demo mode** with fictional
data. When Render asks, paste an Anthropic API key into `ANTHROPIC_API_KEY`, or leave it empty
to use the offline rules. AI use is capped per visitor per hour (`AI_HOURLY_LIMIT_PER_IP`) and per
day (`AI_DAILY_LIMIT`). Also set a monthly spend limit in the Anthropic Console.

On the free plan the service sleeps after about 15 minutes idle, so the first visit after that
takes about 30 seconds. The demo data starts fresh whenever the service restarts. Never set
`DEMO_MODE=false` on a public deployment.

## What happens automatically

| You do | The app does |
|---|---|
| Set **Needs Scheduling** (one record or bulk) | Emails the user the Bookings link. Adds a *Create ticket* to-do with the ticket text prefilled. Starts the reminder clock. |
| *(nothing — user doesn't book)* | Sends a reminder every `REMINDER_INTERVAL_DAYS`, up to `MAX_REMINDERS` times. After that it emails the **Dept contact email** (cc'ing the user), or flags the record for manual follow-up. |
| User books in Bookings | The record moves to **Scheduled** with the date, and the user gets a confirmation. A cancellation moves it back and sends a "please rebook" email. |
| Set **Deployed** | Adds a *Collect old device* to-do, unless the device has already come back. |
| Mark **Old device returned = Yes** | The record moves to **Complete** and a *Close ticket* to-do is added. |

The **To-Do** tab collects everything that still needs a person: tickets to create or close,
users who aren't responding, devices to collect, missing emails, and today's and tomorrow's
appointments.

## Safety defaults

- `EMAIL_MODE=outbox`: emails are saved and shown in the **Outbox** tab, but **nothing is
  sent**. When you switch to `smtp`, set `REDIRECT_ALL_EMAIL_TO=you@uw.edu` first, so every
  email goes to you while you test with real data.
- ServiceNow is never called directly (you only have agent access). Tickets stay a one-click
  copy, paste and save step.
- The data stays in `data/deploytrack.db` on this machine. It contains staff names and NetIDs,
  so don't commit it or share it.

See `.env.example` for every setting.

## Hooking up Microsoft Bookings (optional, after the demo)

Bookings can't call DeployTrack by itself. A small Power Automate flow can:

1. **Trigger:** *Microsoft Bookings → When an appointment is created*. Point it at your Bookings
   page address. Add a second flow for *When an appointment is cancelled*.
2. **Action:** *HTTP → POST* to `https://<where-deploytrack-runs>/api/bookings/webhook`, with the
   header `X-Webhook-Secret: <BOOKINGS_WEBHOOK_SECRET>` and this body:
   ```json
   {"event": "created", "customer_email": "@{triggerOutputs()?['body/CustomerEmail']}",
    "start": "@{triggerOutputs()?['body/StartDate']}", "end": "@{triggerOutputs()?['body/EndDate']}"}
   ```
   Check the exact field names in the trigger's dynamic content.

Two things to check first:
- **Licensing.** The HTTP action is a *Premium* connector, so find out whether your Power
  Automate license includes it.
- **A public address.** DeployTrack has to be reachable from the internet, not just
  `127.0.0.1`, so it needs to run on a team server or VM.

Until then, use **Booked by phone / in person** (or the demo button) on the record.

## AI features (Purple or Claude)

| Feature | Where | What it does |
|---|---|---|
| **Ask Purple** | header button | Plain-English questions ("Who in Phase 2 hasn't booked?", "What's on today?"). Deployment IDs in answers are clickable. |
| **Draft a personal follow-up** | record → Purple assist (Needs Scheduling) | Writes a nudge using the person's notes and history. You edit and click Send. |
| **Interpret reply** | record → Purple assist | Paste a user's email reply. Suggests a status, an appointment time, a note and a reply. Nothing changes until you click **Apply**. |

A person approves every AI action. Nothing is sent or changed by the AI on its own.

### Using Claude instead (works today)

1. Create an API key at [console.anthropic.com](https://console.anthropic.com) under **API Keys**,
   and add a few dollars of credit. A Claude.ai subscription doesn't include API access.
2. In `.env`, set `AI_PROVIDER=claude` and `ANTHROPIC_API_KEY=sk-ant-...`. Restart `./run.sh`.
   The badge should change to **AI: Claude**.

It uses `claude-opus-5-5` through the official `anthropic` SDK, with structured JSON output for
replies and drafts. If the model declines a request, the API retries it on a fallback model
automatically. Check that sending staff data to Claude is allowed under your data policies
before using real records. Demo mode only sends fake people.

### Connecting Purple (personal access token, no admin needed)

DeployTrack *calls* Purple, so it works from your laptop with no hosting:

1. In NebulaOne, go to **Access Tokens → New Token**, and copy it.
2. Open **API reference** and find the chat endpoint: its URL, how the token is sent, and the
   model names.
3. Copy `.env.example` to `.env` and set `AI_PROVIDER=openai`, `AI_BASE_URL`, `AI_CHAT_PATH`,
   `AI_TOKEN` and `AI_MODEL`. Restart `./run.sh`. The header badge should change to **AI: Purple**.

If NebulaOne's request format isn't OpenAI-style, the adapter is the `_chat()` function in
`app/ai.py`, about 30 lines. What's sent to Purple: names, departments, statuses, dates and
notes. Serial numbers aren't sent. A personal token acts as you, so for real team use, ask an
admin for an **API Project**.

Without a token, the AI features run on **offline rules**. They're labelled that way in the UI,
so the demo still works if the connection fails.

## Letting Purple call DeployTrack (later)

The reverse direction: Purple calls DeployTrack's REST API as tools. Give it the OpenAPI spec at `/openapi.json` (the
human-readable version is at `/docs`), and set `API_KEY` so Purple sends `X-API-Key`.

Useful tools:

| Question / command | Endpoint |
|---|---|
| "How is Phase 2 going?" | `GET /api/summary?phase=2` |
| "Who hasn't scheduled yet?" | `GET /api/deployments?status=Needs Scheduling` |
| "What's the status of Jane Doe's laptop?" | `GET /api/deployments?q=jane` then `GET /api/deployments/{id}` |
| "What do I need to do today?" | `GET /api/todo` |
| "Send D150 the scheduling email" | `POST /api/deployments/D150/status` `{"status":"Needs Scheduling","actor":"purple"}` |
| "Note on D150: wants a 2nd monitor" | `POST /api/deployments/D150/notes` |

Purple also needs a reachable address for DeployTrack, not `127.0.0.1`.

## Layout

```
app/main.py          API + serves the UI
app/ai.py            Purple connection, prompts, offline fallback rules
app/demo_data.py     fake dataset generator
app/workflow.py      statuses, automations, email wording, reminders, to-do list
app/integrations.py  email (outbox/SMTP) and ServiceNow (manual/email)
app/importer.py      Excel/CSV import + export
app/db.py            SQLite schema
app/static/          web UI
tests/test_flow.py   end-to-end workflow test
```
