"""Email allowlist. Entries come from Streamlit secrets and/or allowed_emails.txt.
Use "@domain.com" to allow a whole domain; "#" starts a comment."""
import re
from pathlib import Path


def _entries(extra):
    f = Path(__file__).with_name("allowed_emails.txt")
    lines = list(extra) + (f.read_text(encoding="utf-8").splitlines() if f.exists() else [])
    cleaned = (line.split("#", 1)[0].strip().lower() for line in lines)
    return {line for line in cleaned if line}


def is_allowed(email, extra=()):
    email = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return False
    entries = _entries(extra)
    return email in entries or "@" + email.rsplit("@", 1)[1] in entries
