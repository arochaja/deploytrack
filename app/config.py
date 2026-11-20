"""Settings, read from environment variables or a .env file in the project root."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)



# Team / messaging
TEAM_NAME = _get("TEAM_NAME", "UWF Tech Team")
TEAM_EMAIL = _get("TEAM_EMAIL", "uwftech@uw.edu")
BOOKINGS_URL = _get("BOOKINGS_URL", "https://outlook.office.com/bookwithme/REPLACE_ME")
EMAIL_DOMAIN = _get("EMAIL_DOMAIN", "uw.edu")  # netid -> netid@uw.edu

# Email delivery: "outbox" (store only, never sends) or "smtp"
EMAIL_MODE = _get("EMAIL_MODE", "outbox")
SMTP_HOST = _get("SMTP_HOST", "smtp.office365.com")
SMTP_PORT = int(_get("SMTP_PORT", "587"))
SMTP_USER = _get("SMTP_USER")
SMTP_PASSWORD = _get("SMTP_PASSWORD")
# Safety net for testing: when set, every email goes here instead of the real recipient.
REDIRECT_ALL_EMAIL_TO = _get("REDIRECT_ALL_EMAIL_TO")

# ServiceNow: "manual" (to-do item with prefilled text) or "email" (send to an intake address)
SNOW_MODE = _get("SNOW_MODE", "manual")
SNOW_INBOUND_EMAIL = _get("SNOW_INBOUND_EMAIL", "help@uw.edu")

# Reminders
REMINDER_INTERVAL_DAYS = float(_get("REMINDER_INTERVAL_DAYS", "3"))
MAX_REMINDERS = int(_get("MAX_REMINDERS", "2"))  # after this many, escalate to dept contact
REMINDER_CHECK_MINUTES = float(_get("REMINDER_CHECK_MINUTES", "60"))

# Security: when set, /api/* requires header X-API-Key (UI asks once, Purple sends it).
API_KEY = _get("API_KEY")
BOOKINGS_WEBHOOK_SECRET = _get("BOOKINGS_WEBHOOK_SECRET")

DEMO_MODE = _get("DEMO_MODE", "true").lower() == "true"

# Demo mode uses its own fake-data database, so real data is never touched or reset.
DB_PATH = Path(_get("DB_PATH", str(ROOT / "data" / ("demo.db" if DEMO_MODE else "deploytrack.db"))))
DEMO_DB = DB_PATH.name.startswith("demo")

# --- AI (Purple / NebulaOne) ------------------------------------------------
# "offline" = built-in rules, no AI service (labelled in the UI).
# "openai" = an OpenAI-style chat completions API (check NebulaOne's API reference).
# "claude" = Anthropic's Claude API.
AI_PROVIDER = _get("AI_PROVIDER", "offline")
AI_BASE_URL = _get("AI_BASE_URL").rstrip("/")
AI_CHAT_PATH = _get("AI_CHAT_PATH", "/chat/completions")
AI_TOKEN = _get("AI_TOKEN")
AI_MODEL = _get("AI_MODEL")
AI_AUTH_HEADER = _get("AI_AUTH_HEADER", "Authorization")
AI_AUTH_PREFIX = _get("AI_AUTH_PREFIX", "Bearer")  # "" to send the raw token
AI_TIMEOUT = float(_get("AI_TIMEOUT", "60"))
AI_NAME = _get("AI_NAME", "Claude" if AI_PROVIDER == "claude" else "Purple")

# AI_PROVIDER=claude uses the Anthropic API (needs a key from console.anthropic.com).
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _get("CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = _get("CLAUDE_EFFORT", "medium")  # low | medium | high

# No key yet (e.g. a fresh deploy): use the free offline rules instead of failing.
if AI_PROVIDER == "claude" and not (ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY")):
    AI_PROVIDER = "offline"

# Spending guard for public deployments: AI calls allowed per visitor per hour, and per day in total.
AI_HOURLY_LIMIT_PER_IP = int(_get("AI_HOURLY_LIMIT_PER_IP", "30"))
AI_DAILY_LIMIT = int(_get("AI_DAILY_LIMIT", "400"))
